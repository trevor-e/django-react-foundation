"""The token mechanics behind password reset and email verification.

Every test here is about a way to hand someone else's account away: a link that
resolves to the wrong user, a token that outlives its expiry, a preview that spends the
link it was only meant to inspect, a token minted for one purpose accepted by another.
"""

import pytest
from django.contrib.auth import get_user_model

from drf_foundation.accounts import PasswordResetLink, SignedUserToken

pytestmark = pytest.mark.django_db

User = get_user_model()
RESET = PasswordResetLink()
VERIFY = SignedUserToken(salt="test.verify", max_age_seconds=3600)


@pytest.fixture
def user():
    return User.objects.create_user(username="alice", email="a@example.test", password="pw-aaaa")


@pytest.fixture
def other():
    return User.objects.create_user(username="bob", email="b@example.test", password="pw-bbbb")


# --- password reset ----------------------------------------------------------


def test_a_link_resolves_to_its_own_user(user):
    uid, token = RESET.make(user)
    assert RESET.resolve(User, uid, token) == user


def test_one_users_token_does_not_open_anothers_account(user, other):
    _, token = RESET.make(user)
    other_uid, _ = RESET.make(other)

    assert RESET.resolve(User, other_uid, token) is None


def test_a_garbage_uid_denies_rather_than_raises(user):
    _, token = RESET.make(user)
    for uid in ("", "!!!!", "notbase64", "\x00", "999999999999999999999999"):
        assert RESET.resolve(User, uid, token) is None


def test_a_tampered_token_is_refused(user):
    uid, token = RESET.make(user)
    assert RESET.resolve(User, uid, token[:-1] + ("a" if token[-1] != "a" else "b")) is None


def test_completing_a_reset_invalidates_the_link(user):
    uid, token = RESET.make(user)
    user.set_password("a-different-password")
    user.save()

    assert RESET.resolve(User, uid, token) is None


def test_checking_a_token_does_not_consume_it(user):
    """A preview endpoint must be able to show whose account is being reset without
    spending the link — otherwise looking at the page breaks it."""
    uid, token = RESET.make(user)

    assert RESET.resolve(User, uid, token) == user
    assert RESET.resolve(User, uid, token) == user
    assert RESET.resolve(User, uid, token) == user


# --- signed user tokens ------------------------------------------------------


def test_a_signed_token_round_trips(user):
    assert VERIFY.load(User, VERIFY.make(user)) == user


def test_a_token_from_another_salt_is_refused(user):
    """Salts are what keep one flow's token from being spent on another."""
    other_purpose = SignedUserToken(salt="test.something-else", max_age_seconds=3600)

    assert VERIFY.load(User, other_purpose.make(user)) is None


def test_an_expired_token_is_refused(user):
    instant = SignedUserToken(salt="test.verify", max_age_seconds=-1)

    assert instant.load(User, instant.make(user)) is None


def test_a_tampered_token_is_refused_too(user):
    token = VERIFY.make(user)
    assert VERIFY.load(User, token[:-1] + ("a" if token[-1] != "a" else "b")) is None


def test_garbage_denies_rather_than_raises():
    for token in ("", "nonsense", "a:b:c", "\x00"):
        assert VERIFY.load(User, token) is None


def test_a_token_survives_an_email_change(user):
    """The payload binds the user id, not the address — so changing the address does
    not silently retarget an outstanding link at whoever takes the old one."""
    token = VERIFY.make(user)
    user.email = "moved@example.test"
    user.save()

    assert VERIFY.load(User, token) == user


def test_a_deleted_user_denies(user):
    token = VERIFY.make(user)
    user.delete()

    assert VERIFY.load(User, token) is None


# --- primary key types -------------------------------------------------------


def test_a_uuid_primary_key_round_trips(monkeypatch):
    """A UUID pk is as common as an integer one, and the payload is JSON — so the token
    has to carry a string, not the key object. Found by the second consumer, whose User
    is UUID-keyed; the first one's is an int and never exercised this."""
    import uuid as uuidlib

    class FakeManager:
        def __init__(self, obj):
            self._obj = obj

        def get(self, pk):
            if str(pk) != str(self._obj.pk):
                raise FakeUser.DoesNotExist
            return self._obj

    class FakeUser:
        class DoesNotExist(Exception):
            pass

        def __init__(self):
            self.pk = uuidlib.uuid4()

    obj = FakeUser()
    FakeUser._default_manager = FakeManager(obj)

    token = VERIFY.make(obj)
    assert VERIFY.load(FakeUser, token) is obj


def test_a_payload_that_is_not_a_valid_key_denies():
    """A well-formed token whose payload cannot be a pk must deny, not raise — a UUID
    field raises ValidationError rather than ValueError for that."""

    class FakeUser:
        class DoesNotExist(Exception):
            pass

    class Raising:
        def get(self, pk):
            from django.core.exceptions import ValidationError

            raise ValidationError("badly formed UUID")

    FakeUser._default_manager = Raising()

    class Obj:
        pk = "not-a-uuid"

    assert VERIFY.load(FakeUser, VERIFY.make(Obj())) is None


def test_a_uid_that_is_not_a_valid_key_denies_rather_than_raises():
    """A UUID-keyed project raises ValidationError, not ValueError, for a uid that
    decodes to something that is not a UUID. Missing it turns an attacker-supplied uid
    into a 500 on a public endpoint — which is exactly what shipped in 0.19.1."""

    class FakeUser:
        class DoesNotExist(Exception):
            pass

    class Raising:
        def get(self, pk):
            from django.core.exceptions import ValidationError

            raise ValidationError(f"“{pk}” is not a valid UUID.")

    FakeUser._default_manager = Raising()

    for uid in ("!!!", "", "Zm9v", "----"):
        assert RESET.resolve(FakeUser, uid, "any-token") is None
