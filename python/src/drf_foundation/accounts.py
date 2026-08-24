"""Password-reset and email-verification token mechanics.

What this module is, and is not: it is the *credential* half of the account flows, not
the views. Reading two real implementations of register/verify/reset side by side, the
view bodies turn out to be almost entirely product decisions — a bot check, an invite
token, which audit verb to record, which notification kind to send, which template
renders the mail. A shared view would need an injection point for each, and a caller
would supply more code configuring it than the copy it replaced.

What *is* identical in both, line for line, is the token handling. It is also the part
where a mistake is an account takeover rather than a cosmetic difference: a reset link
that resolves to the wrong user, a verification token that survives expiry, a preview
endpoint that consumes the token it was only supposed to inspect. So that is what lives
here, and the views stay in the project where their product decisions belong.

Both helpers deliberately collapse *every* failure — malformed, unknown, tampered,
expired — to ``None``. A caller that cannot distinguish them cannot accidentally leak
which one happened, and "invalid or expired" is the only thing a user should be told.
"""

from dataclasses import dataclass
from typing import Any

from django.contrib.auth.tokens import PasswordResetTokenGenerator, default_token_generator
from django.core import signing
from django.core.exceptions import ValidationError
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_decode, urlsafe_base64_encode


@dataclass(frozen=True)
class PasswordResetLink:
    """The ``uid``/``token`` pair a password-reset link carries.

    Wraps Django's own generator rather than inventing a scheme, which buys three
    properties worth naming because a hand-rolled version usually loses one:

    - **Single use.** The token hashes the current password and ``last_login``, so
      completing a reset (or logging in) invalidates every outstanding link.
    - **Stateless.** No table, no cleanup job — and *checking* a token never consumes
      it, which is what lets a preview endpoint show whose account is being reset
      without spending the link.
    - **Expiring**, via ``settings.PASSWORD_RESET_TIMEOUT``.
    """

    generator: PasswordResetTokenGenerator = default_token_generator

    def make(self, user: Any) -> tuple[str, str]:
        """``(uid, token)`` for this user's reset link."""
        return urlsafe_base64_encode(force_bytes(user.pk)), self.generator.make_token(user)

    def resolve(self, user_model: type, uid: str, token: str) -> Any | None:
        """The user this pair identifies, or ``None`` on any failure.

        ``uid`` is attacker-controlled, so a value that is not valid base64, not an
        integer, or names no user has to deny rather than raise — otherwise a
        hand-crafted request is a 500 instead of a 400.
        """
        try:
            pk = urlsafe_base64_decode(uid).decode()
            user = user_model._default_manager.get(pk=pk)
        except (TypeError, ValueError, OverflowError, user_model.DoesNotExist):
            return None
        if not self.generator.check_token(user, token):
            return None
        return user


@dataclass(frozen=True)
class SignedUserToken:
    """A signed, expiring token bound to a user id.

    For the flows with no revocation story — email verification, unsubscribe links —
    where a token table would be pure cost. ``django.core.signing`` supplies tamper
    proofing and expiry with nothing to clean up.

    The payload carries the **user id, never the email string**, so a token cannot be
    replayed onto another account after an address change.

    ``salt`` must differ per purpose. Two flows sharing one salt means a token minted
    for the cheaper one is accepted by the more privileged one.
    """

    salt: str
    max_age_seconds: int

    def make(self, user: Any) -> str:
        # ``str(pk)``, not the pk itself: the payload is JSON, and a UUID primary key —
        # as common as an integer one — is not JSON-serializable. Stringifying here
        # rather than at the call site keeps the token shape identical whatever the
        # project's key type is, and the ORM coerces the string back on lookup.
        return signing.dumps({"user_id": str(user.pk)}, salt=self.salt)

    def load(self, user_model: type, token: str) -> Any | None:
        """The user this token was issued for, or ``None`` on any failure.

        ``SignatureExpired`` subclasses ``BadSignature``, so one except covers tampered
        and stale alike — which is also the only distinction worth *not* making, since
        telling them apart tells an attacker whether a token was ever real.
        """
        try:
            payload = signing.loads(token, salt=self.salt, max_age=self.max_age_seconds)
        except signing.BadSignature:
            return None
        if not isinstance(payload, dict):
            return None
        user_id = payload.get("user_id")
        if user_id is None:
            return None
        try:
            return user_model._default_manager.get(pk=user_id)
        except (TypeError, ValueError, ValidationError, user_model.DoesNotExist):
            # ValidationError is what a UUID field raises for a well-formed token whose
            # payload is not a UUID — deny, rather than 500 on a hand-crafted request.
            return None
