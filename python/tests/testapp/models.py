"""Concrete models exercising the package's abstract bases (tables are created by the
test runner's syncdb pass — this app ships no migrations, like the package itself)."""

from django.db import models

from drf_foundation.event_log import EventLogEntry
from drf_foundation.mcp.api_keys import AbstractApiKey


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


class ApiKey(AbstractApiKey):
    """The concrete key model: the base's mechanics plus this app's own identity."""

    account = models.ForeignKey(Account, related_name="api_keys", on_delete=models.CASCADE)
    name = models.CharField(max_length=100)
    scope = models.CharField(max_length=16, default="read_write")
