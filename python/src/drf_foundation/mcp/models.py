"""Abstract model bases for the OAuth surface.

The package ships no migrations (the rule the whole of ``drf_foundation`` keeps),
so these are bases a project subclasses, adds its own foreign keys to, and owns
the migration for — the same arrangement as
:class:`drf_foundation.event_log.EventLogEntry`.

Everything the package itself reads lives on these bases. What a project must
declare, and the names :mod:`drf_foundation.mcp.oauth` looks them up by:

======================  ==========================================================
``client``              FK on the code and grant models, to the concrete client
``resource``            FK on the code and grant models, to whatever a token grants
                        access to — a tenant row, or the user in a single-tenant app
``issued_key``          Nullable FK on the code model, to the concrete key model
``api_key``             OneToOne on the grant model, to the concrete key model
``id``                  Whatever primary key the project uses
======================  ==========================================================

A concrete set looks like::

    class OAuthClient(AbstractOAuthClient):
        id = models.UUIDField(primary_key=True, default=uuid7, editable=False)

    class AuthorizationCode(AbstractAuthorizationCode):
        id = models.UUIDField(primary_key=True, default=uuid7, editable=False)
        client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="codes")
        resource = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="+")
        issued_key = models.ForeignKey(
            ApiKey, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
        )

Why FKs rather than an opaque ``resource_key`` string the package could own
outright: these rows *grant access*. A deleted tenant has to take its live grants
with it, and only a real foreign key does that.
"""

from typing import Any

from django.conf import settings
from django.db import models


class AbstractOAuthClient(models.Model):
    """A dynamically registered OAuth client (RFC 7591).

    Public clients only — no secret is ever issued, so possession of a
    ``client_id`` grants nothing without a user completing consent. That is what
    makes open registration safe: a row here is an announcement, not a capability.
    """

    # Projects declare their own primary key (UUIDv7, bigint, whatever). The bare
    # annotation is not a field — it stops a type checker synthesizing `id: int`
    # here and then calling a subclass's UUID pk an inconsistent override.
    id: Any

    client_id = models.CharField(max_length=64, unique=True)
    name = models.CharField(max_length=100)
    redirect_uris = models.JSONField(default=list)
    created_at = models.DateTimeField(auto_now_add=True)
    last_grant_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return f"{self.name} ({self.client_id})"


class AbstractAuthorizationCode(models.Model):
    """A single-use authorization code, hashed at rest and short-lived.

    Bound to everything the token exchange must re-verify: the client it was
    issued to, the exact redirect URI, the PKCE challenge, the resource and user
    consented to, and the scope. ``used_at`` plus ``issued_key`` are what make a
    replay detectable *and* punishable — OAuth 2.1 asks that a reused code revoke
    whatever it minted the first time.
    """

    # Projects declare their own primary key (UUIDv7, bigint, whatever). The bare
    # annotation is not a field — it stops a type checker synthesizing `id: int`
    # here and then calling a subclass's UUID pk an inconsistent override.
    id: Any

    code_hash = models.CharField(max_length=64, unique=True)
    redirect_uri = models.CharField(max_length=500)
    code_challenge = models.CharField(max_length=128)
    scope = models.CharField(max_length=16)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return f"authorization code ({self.scope})"


class AbstractGrant(models.Model):
    """Links a minted key to the client and resource it was consented for.

    Its whole job is replace-on-reconnect: without it, every reconnection from the
    same client leaves another live credential behind.
    """

    # Projects declare their own primary key (UUIDv7, bigint, whatever). The bare
    # annotation is not a field — it stops a type checker synthesizing `id: int`
    # here and then calling a subclass's UUID pk an inconsistent override.
    id: Any

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True

    def __str__(self) -> str:
        return f"grant {self.pk}"
