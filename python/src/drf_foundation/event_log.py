"""Ordered per-scope event logs: the server half of cursor sync.

The pattern (pairs with ``createCursorSync`` in the JS package): a feature appends
immutable facts to a scoped log with a monotonically increasing ``seq``, publishes the
new head to the scope's realtime channel *after commit*, and serves reads as
``events?after=<cursor>`` — ordered, gap-free, page-capped. Clients hold a cursor and
fetch forward; reconnect, catch-up after a hidden tab, spectating, and replay are all
the same read with a different ``after``. SSE stays a doorbell (the head seq), Postgres
stays the mailbox.

This module ships no concrete model (the package has no migrations, by design).
Projects subclass :class:`EventLogEntry`, add their scope FK and a unique
``(scope, seq)`` constraint, and own the migration:

    class GameEvent(EventLogEntry):
        war = models.ForeignKey(War, related_name="events", on_delete=models.CASCADE)

        class Meta(EventLogEntry.Meta):
            constraints = [
                models.UniqueConstraint(fields=["war", "seq"], name="uniq_war_seq"),
            ]

**The caller owns scope-level mutual exclusion.** :func:`append_events` computes the
next seq as ``MAX(seq) + 1`` over the queryset you pass; two uncoordinated writers can
compute the same head. Serialize writers per scope — ``select_for_update()`` on the
scope row is the intended pattern — and keep the unique constraint as the backstop that
turns a violated lock protocol into an ``IntegrityError`` instead of silent seq reuse.
"""

from collections.abc import Sequence

from django.db import models, transaction
from rest_framework.request import Request

from drf_foundation.realtime import publish
from drf_foundation.schemas import RequestValidationError


class EventLogEntry(models.Model):
    """Abstract base for one scope's append-only event log.

    ``seq`` starts at 1 and is contiguous per scope; ``payload`` is the event's full
    JSON body (shape owned by the project's wire schemas, not by this table).
    """

    seq = models.BigIntegerField()
    event_type = models.CharField(max_length=100)
    payload = models.JSONField(default=dict)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        abstract = True
        ordering = ["seq"]


def head_seq(qs: models.QuerySet) -> int:
    """The scope's latest seq, or 0 when the log is empty."""
    return qs.aggregate(head=models.Max("seq"))["head"] or 0


def append_events(
    qs: models.QuerySet,
    rows: Sequence[tuple[str, dict]],
    *,
    extra_fields: dict[str, object] | None = None,
) -> tuple[int, int]:
    """Append ``rows`` (``(event_type, payload)`` pairs) with contiguous seqs.

    ``qs`` scopes the log (e.g. ``war.events.all()``) and routes the write to its DB;
    ``extra_fields`` is stamped onto every row (the scope FK, audit columns). Returns
    ``(first_seq, last_seq)``. Raises ``ValueError`` on an empty batch — appending
    nothing is always a caller bug.

    The caller MUST hold the scope's lock (see module docstring).
    """
    if not rows:
        raise ValueError("append_events called with no rows")
    model = qs.model
    start = head_seq(qs) + 1
    fields = extra_fields or {}
    objs = [
        model(seq=start + i, event_type=event_type, payload=payload, **fields)
        for i, (event_type, payload) in enumerate(rows)
    ]
    model._default_manager.using(qs.db).bulk_create(objs)
    return start, start + len(objs) - 1


def events_after(qs: models.QuerySet, after: int, *, limit: int = 500) -> list[EventLogEntry]:
    """One ordered page of the scope's events with ``seq > after`` (max ``limit``).

    Readers repeat with the last received seq as the new cursor until an empty page —
    at which point they are exactly caught up.
    """
    return list(qs.filter(seq__gt=after).order_by("seq")[:limit])


def after_param(request: Request, *, name: str = "after") -> int:
    """Parse the cursor query parameter: absent → 0; malformed/negative → envelope 400.

    Raises :class:`~drf_foundation.schemas.RequestValidationError`, which the package's
    exception handler renders as ``{"status": "error", ...}`` with HTTP 400.
    """
    raw = request.query_params.get(name)
    if raw is None or raw == "":
        return 0
    try:
        value = int(raw)
    except ValueError:
        raise RequestValidationError(f"{name}: must be a non-negative integer") from None
    if value < 0:
        raise RequestValidationError(f"{name}: must be a non-negative integer")
    return value


def publish_after_commit(
    redis_url: str, channel: str, message: str, *, using: str | None = None
) -> None:
    """Publish to the scope's channel only once the surrounding transaction commits.

    Rides ``transaction.on_commit`` (so a rollback publishes nothing) and the fail-soft
    :func:`~drf_foundation.realtime.publish` (so a Redis outage never breaks the write
    path it piggybacks on — cursor clients catch up by polling instead).
    """
    transaction.on_commit(lambda: publish(redis_url, channel, message), using=using)
