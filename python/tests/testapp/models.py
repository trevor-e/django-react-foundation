"""Concrete models exercising the package's abstract bases (tables are created by the
test runner's syncdb pass — this app ships no migrations, like the package itself)."""

from django.conf import settings
from django.db import models

from drf_foundation.event_log import EventLogEntry
from drf_foundation.mcp.api_keys import AbstractApiKey
from drf_foundation.mcp.models import (
    AbstractAuthorizationCode,
    AbstractGrant,
    AbstractOAuthClient,
)


class Stream(models.Model):
    """A minimal scope row for event-log tests (stand-in for a war/room/tenant)."""

    name = models.CharField(max_length=50)


class StreamEvent(EventLogEntry):
    stream = models.ForeignKey(Stream, related_name="events", on_delete=models.CASCADE)

    class Meta(EventLogEntry.Meta):
        constraints = [
            models.UniqueConstraint(fields=["stream", "seq"], name="uniq_stream_seq"),
        ]


class Account(models.Model):
    """A stand-in for whatever an API key is scoped to (household, org, user)."""

    name = models.CharField(max_length=50)
    members = models.ManyToManyField(settings.AUTH_USER_MODEL, related_name="accounts", blank=True)

    def __str__(self) -> str:
        return self.name


class ApiKey(AbstractApiKey):
    """The concrete key model: the base's mechanics plus this app's own identity."""

    account = models.ForeignKey(Account, related_name="api_keys", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    scope = models.CharField(max_length=16, default="read_write")


# --- OAuth: two concrete model sets, one per tenancy shape -------------------
#
# The multi-tenant set grants against an Account (a household/org/workspace); the
# single-tenant set grants against the user themselves. Both are real tables, so
# the suite proves the resource FK can point at either — the second is exactly the
# shape a per-user app has.


class OAuthClient(AbstractOAuthClient):
    pass


class AuthorizationCode(AbstractAuthorizationCode):
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="codes")
    resource = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="+")
    issued_key = models.ForeignKey(
        ApiKey, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )


class Grant(AbstractGrant):
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="grants")
    resource = models.ForeignKey(Account, on_delete=models.CASCADE, related_name="+")
    api_key = models.OneToOneField(ApiKey, on_delete=models.CASCADE, related_name="grant")


class UserAuthorizationCode(AbstractAuthorizationCode):
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="user_codes")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    issued_key = models.ForeignKey(
        ApiKey, on_delete=models.SET_NULL, null=True, blank=True, related_name="+"
    )


class UserGrant(AbstractGrant):
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="user_grants")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="+")
    api_key = models.OneToOneField(ApiKey, on_delete=models.CASCADE, related_name="user_grant")
