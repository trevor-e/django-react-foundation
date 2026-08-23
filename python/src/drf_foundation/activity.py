"""Activity logs: the safe write and the cursor read, over an event log.

:mod:`drf_foundation.event_log` supplies the table shape — ordered, append-only,
per-scope facts. What it deliberately does not supply is the discipline for using
one as an *activity or audit trail*, and that discipline is the part every project
gets to re-derive:

- **Appends must be serialized per scope.** ``append_events`` computes the next
  sequence as ``MAX(seq) + 1`` and does not lock; two uncoordinated writers compute
  the same head, and the unique constraint turns that into an ``IntegrityError``
  rather than a silent duplicate. The scope row is what has to be locked, and
  forgetting is invisible until two things happen at once in production.
- **Reads are a cursor, not a page number.** Callers hold the last sequence they
  saw and fetch forward.

:class:`ActivityLog` is those two things, configured once per project::

    ACTIVITY = ActivityLog(AccountEvent, scope_field="user")
    ACTIVITY.record(user, "mcp.connected", {"client": "Claude"})
    ACTIVITY.entries(user, after=cursor)

**Cost worth knowing before the vocabulary grows.** Contiguous sequence numbers are
what make the log cursor-readable, and they are also what forces the lock — so every
append takes a row lock on the scope. At human-scale events (a connection, a
password change) that is invisible. At high-frequency writes it is not: every
audited write serializes on that row. An audit trail that never needs cursor reads
would be cheaper with a plain autoincrement; this is the trade being made.
"""

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

from django.db import models, transaction

from drf_foundation.event_log import EventLogEntry, append_events, events_after, head_seq

DEFAULT_PAGE_LIMIT = 100


@dataclass(frozen=True)
class ActivityLog:
    """One project's activity log: a concrete :class:`EventLogEntry` plus its scope.

    ``scope_field`` names the FK on the log model pointing at whatever the log is
    per — a user, a household, a workspace. The scope object itself is what gets
    locked on append, so it must be a saved model instance.
    """

    model: type[EventLogEntry]
    scope_field: str = "user"

    def scoped(self, scope: models.Model) -> models.QuerySet:
        """This scope's entries, oldest first."""
        return self.model._default_manager.filter(**{self.scope_field: scope})

    def head(self, scope: models.Model) -> int:
        """The scope's latest sequence, or 0 when the log is empty."""
        return head_seq(self.scoped(scope))

    def record(
        self, scope: models.Model, event_type: str, payload: dict[str, Any] | None = None
    ) -> EventLogEntry:
        """Append one event, holding the scope's lock. Returns the stored entry.

        Payloads describe what happened in terms a person can act on. They must not
        reproduce credential secrets — reference a credential by a masked prefix.
        """
        first, _ = self.record_many(scope, [(event_type, payload or {})])
        return self.scoped(scope).get(seq=first)

    def record_many(
        self, scope: models.Model, rows: Sequence[tuple[str, dict[str, Any]]]
    ) -> tuple[int, int]:
        """Append a batch atomically. Returns ``(first_seq, last_seq)``.

        The lock is taken on the *scope* row rather than on the log, because the next
        sequence is derived from the log and what must not race is another append for
        this same scope.
        """
        with transaction.atomic():
            locked = type(scope)._default_manager.select_for_update().get(pk=scope.pk)
            return append_events(self.scoped(locked), rows, extra_fields={self.scope_field: locked})

    def entries(
        self, scope: models.Model, *, after: int = 0, limit: int = DEFAULT_PAGE_LIMIT
    ) -> list[EventLogEntry]:
        """One ordered page of entries after ``after`` — an empty page means caught up."""
        return events_after(self.scoped(scope), after, limit=limit)
