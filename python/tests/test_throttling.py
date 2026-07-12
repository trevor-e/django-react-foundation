from types import SimpleNamespace
from unittest.mock import patch

from rest_framework.authtoken.models import Token
from rest_framework.throttling import UserRateThrottle

from drf_foundation.throttling import (
    LoginRateThrottle,
    RegisterRateThrottle,
    TokenUserRateThrottle,
)


def test_login_rate_throttle_scope():
    assert LoginRateThrottle.scope == "auth-login"


def test_register_rate_throttle_scope():
    assert RegisterRateThrottle.scope == "auth-register"


def test_token_user_rate_throttle_scope():
    assert TokenUserRateThrottle.scope == "token-user"


def test_bypasses_non_token_auth():
    # JWT requests (SimpleJWT auth) and shared-key/anonymous requests carry no
    # Token instance as request.auth — none of them should be throttled here.
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
