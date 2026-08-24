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
