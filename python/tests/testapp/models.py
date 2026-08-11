"""Concrete models exercising the package's abstract bases (tables are created by the
test runner's syncdb pass — this app ships no migrations, like the package itself)."""

from django.db import models

from drf_foundation.event_log import EventLogEntry


class Stream(models.Model):
    """A minimal scope row for event-log tests (stand-in for a war/room/tenant)."""

    name = models.CharField(max_length=50)


class StreamEvent(EventLogEntry):
    stream = models.ForeignKey(Stream, related_name="events", on_delete=models.CASCADE)

    class Meta(EventLogEntry.Meta):
        constraints = [
            models.UniqueConstraint(fields=["stream", "seq"], name="uniq_stream_seq"),
        ]
