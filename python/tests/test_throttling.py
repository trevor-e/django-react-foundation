from types import SimpleNamespace
from unittest.mock import patch

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import RequestFactory
from rest_framework.authtoken.models import Token
from rest_framework.throttling import UserRateThrottle

from drf_foundation.throttling import (
    CsrfBootstrapRateThrottle,
    IpKeyedThrottle,
    LoginRateThrottle,
    RegisterRateThrottle,
    TokenUserRateThrottle,
)


@pytest.fixture(autouse=True)
def _clear_throttle_buckets():
    # Throttle state lives in the default cache and is keyed per scope+IP, so it
    # leaks across tests unless cleared.
    cache.clear()
    yield
    cache.clear()


def test_login_rate_throttle_scope():
    assert LoginRateThrottle.scope == "auth-login"


def test_register_rate_throttle_scope():
    assert RegisterRateThrottle.scope == "auth-register"


def test_token_user_rate_throttle_scope():
    assert TokenUserRateThrottle.scope == "token-user"


# --- IpKeyedThrottle: the regression these classes exist for --------------------------
#
# Every throttle below used to subclass AnonRateThrottle, whose get_cache_key returns
# None for an authenticated request — which makes allow_request return True, i.e. no
# throttling at all once the caller has a session. Both consumers of this package hit
# that and patched it locally before it was fixed here, so these assert the behavior
# rather than the class names: a name-only test suite is what let the defect ship.


@pytest.mark.parametrize(
    "throttle_class", [LoginRateThrottle, RegisterRateThrottle, CsrfBootstrapRateThrottle]
)
@pytest.mark.django_db
def test_keys_on_ip_even_when_authenticated(throttle_class):
    user = get_user_model().objects.create_user(username="u", password="pw")  # noqa: S106
    request = RequestFactory().post("/", REMOTE_ADDR="203.0.113.7")
    request.user = user

    key = throttle_class().get_cache_key(request, view=None)

    assert key is not None, "an authenticated caller must still be throttled"
    assert "203.0.113.7" in key


@pytest.mark.parametrize(
    "throttle_class", [LoginRateThrottle, RegisterRateThrottle, CsrfBootstrapRateThrottle]
)
@pytest.mark.django_db
def test_same_ip_shares_one_bucket_signed_in_or_out(throttle_class):
    """Signing in must not hand the caller a fresh bucket."""
    factory = RequestFactory()
    anon = factory.post("/", REMOTE_ADDR="198.51.100.4")
    anon.user = SimpleNamespace(is_authenticated=False)
    authed = factory.post("/", REMOTE_ADDR="198.51.100.4")
    authed.user = get_user_model().objects.create_user(username="u2", password="pw")  # noqa: S106

    throttle = throttle_class()
    assert throttle.get_cache_key(anon, view=None) == throttle.get_cache_key(authed, view=None)


@pytest.mark.django_db
def test_register_throttle_actually_blocks_an_authenticated_caller():
    """The case that mattered: signup signs the new user in, so a throttle that
    exempts authenticated requests stops applying after the first signup."""
    user = get_user_model().objects.create_user(username="u3", password="pw")  # noqa: S106

    def _request():
        r = RequestFactory().post("/", REMOTE_ADDR="192.0.2.55")
        r.user = user
        return r

    def _throttle():
        # `rate` is parsed in SimpleRateThrottle.__init__, so the limit is an instance
        # attribute rather than a class one; tighten it per instance to 2/min.
        t = RegisterRateThrottle()
        t.num_requests, t.duration = 2, 60
        return t

    assert _throttle().allow_request(_request(), view=None) is True
    assert _throttle().allow_request(_request(), view=None) is True
    assert _throttle().allow_request(_request(), view=None) is False


def test_ip_keyed_throttle_is_the_shared_base():
    for cls in (LoginRateThrottle, RegisterRateThrottle, CsrfBootstrapRateThrottle):
        assert issubclass(cls, IpKeyedThrottle)


# --- TokenUserRateThrottle -----------------------------------------------------------


def test_bypasses_non_token_auth():
    # Session requests, shared-key ops requests and anonymous reads carry no Token
    # instance as request.auth — none of them should be throttled here.
    request = SimpleNamespace(auth=None)
    throttle = TokenUserRateThrottle()
    assert throttle.allow_request(request, view=None) is True


def test_delegates_to_super_for_token_auth():
    request = SimpleNamespace(auth=Token())
    throttle = TokenUserRateThrottle()
    with patch.object(UserRateThrottle, "allow_request", return_value="sentinel") as mocked:
        result = throttle.allow_request(request, view=None)
    mocked.assert_called_once_with(request, None)
    assert result == "sentinel"
