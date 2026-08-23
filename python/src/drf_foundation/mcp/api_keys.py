"""Long-lived API credentials: minting, hashing, and the one lookup choke point.

An MCP connection needs a bearer credential that outlives a browser session and
can be revoked from a settings page. `rest_framework.authtoken` is the obvious
reach, and it does not fit: it is one token per user, with no scope, no per-client
identity, and the secret stored in plaintext. So this is the small amount of
credential machinery an MCP server actually needs, kept in one place rather than
re-derived per project.

The discipline, which is the reason this is a module and not a snippet:

- **Only a hash is stored.** The secret is shown once, at mint time, and never
  again. Lookup is a single indexed equality on ``sha256(token)``, so it costs one
  index hit and no secret material ever sits in the database.
- **One resolution path.** :func:`resolve_token` is the only way a presented token
  becomes a row. Anything else — a second lookup in a middleware, a "quick check"
  in a view — is how revocation ends up honored in one place and not another.
- **``last_used_at`` is advisory.** It is written at most once per key per
  resolution window, so a read-heavy key does not turn every request into an
  UPDATE.

The model base carries only the credential mechanics. Identity (what the key is
called, who created it, what it is scoped to, what it grants access to) is the
project's, because those differ per app and Django does not permit a subclass to
override a field inherited from an abstract base.
"""

import hashlib
import secrets
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from django.db import models
from django.http import HttpRequest
from django.utils import timezone

# 43 chars of base62 ≈ 256 bits.
_BASE62 = "0123456789ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz"

DEFAULT_SECRET_LENGTH = 43
DEFAULT_DISPLAY_LENGTH = 12
DEFAULT_LAST_USED_RESOLUTION = timedelta(minutes=1)


class AbstractApiKey(models.Model):
    """The credential mechanics of an API key: hash at rest, revoke, last-used.

    Subclass it, add the project's own ``id``, name, scope vocabulary, owner, and
    whatever the key grants access to, and own the migration::

        class ApiKey(AbstractApiKey):
            id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
            account = models.ForeignKey(Account, on_delete=models.CASCADE)
            scope = models.CharField(max_length=16, choices=Scope.choices)

    Deliberately not tenant-scoped by any manager: authentication resolves the
    tenant *from* the key, so the hash lookup necessarily happens before any
    tenant context exists.
    """

    # Projects declare their own primary key (UUIDv7, bigint, whatever). The bare
    # annotation is not a field — it stops a type checker synthesizing `id: int`
    # here and then calling a subclass's UUID pk an inconsistent override.
    id: Any

    key_hash = models.CharField(max_length=64, unique=True)
    # The visible handle (prefix + a few secret chars) shown in settings UIs, so a
    # person can tell two keys apart without ever seeing either secret again.
    key_prefix = models.CharField(max_length=16)
    created_at = models.DateTimeField(auto_now_add=True)
    revoked_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


@dataclass(frozen=True)
class TokenCodec:
    """Generates, hashes, and recognizes one flavor of token.

    ``prefix`` is what makes a token identifiable on sight — in a log, a support
    ticket, or a secret scanner — and lets an authenticator claim the token
    unconditionally rather than letting another auth class try to parse it.
    """

    prefix: str
    secret_length: int = DEFAULT_SECRET_LENGTH
    display_length: int = DEFAULT_DISPLAY_LENGTH

    def generate(self) -> str:
        secret = "".join(secrets.choice(_BASE62) for _ in range(self.secret_length))
        return f"{self.prefix}{secret}"

    def hash(self, token: str) -> str:
        return hashlib.sha256(token.encode()).hexdigest()

    def display_prefix(self, token: str) -> str:
        return token[: self.display_length]

    def owns(self, token: str) -> bool:
        return token.startswith(self.prefix)


def resolve_token[K: AbstractApiKey](
    model: type[K],
    codec: TokenCodec,
    token: str,
    *,
    select_related: tuple[str, ...] = (),
    last_used_resolution: timedelta = DEFAULT_LAST_USED_RESOLUTION,
) -> K | None:
    """The active key for a presented token, or ``None``.

    Touches ``last_used_at`` (rate-capped) on success. This is the single lookup
    choke point — call it once per request, from the authenticator, and nowhere
    else.
    """
    if not codec.owns(token):
        return None
    queryset = model.objects.filter(key_hash=codec.hash(token), revoked_at__isnull=True)
    if select_related:
        queryset = queryset.select_related(*select_related)
    key = queryset.first()
    if key is None:
        return None
    now = timezone.now()
    if key.last_used_at is None or now - key.last_used_at > last_used_resolution:
        model.objects.filter(pk=key.pk).update(last_used_at=now)
        key.last_used_at = now
    return key


def bearer_token(request: HttpRequest) -> str | None:
    """The bearer token from the Authorization header, or ``None``."""
    header = request.META.get("HTTP_AUTHORIZATION", "")
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    return parts[1]
