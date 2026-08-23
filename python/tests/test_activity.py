"""ActivityLog: the safe write and the cursor read.

The lock is the whole reason this class exists, so the concurrency case is the one
that matters most here — the rest of the surface is thin by design.
"""

import pytest
from django.db import models, transaction

from drf_foundation.activity import ActivityLog
from tests.testapp.models import Stream, StreamEvent

pytestmark = pytest.mark.django_db

LOG = ActivityLog(StreamEvent, scope_field="stream")


@pytest.fixture
def stream():
    return Stream.objects.create(name="scope-a")


def test_the_first_event_starts_at_one(stream):
    entry = LOG.record(stream, "thing.happened", {"detail": "x"})

    assert entry.seq == 1
    assert entry.event_type == "thing.happened"
    assert entry.payload == {"detail": "x"}
    assert entry.stream == stream


def test_sequences_are_contiguous(stream):
    for _ in range(3):
        LOG.record(stream, "thing.happened")

    assert [e.seq for e in LOG.entries(stream)] == [1, 2, 3]
    assert LOG.head(stream) == 3


def test_a_payload_may_be_omitted(stream):
    assert LOG.record(stream, "thing.happened").payload == {}


def test_scopes_number_independently(stream):
    other = Stream.objects.create(name="scope-b")

    LOG.record(stream, "a")
    LOG.record(stream, "a")
    first_other = LOG.record(other, "a")

    assert first_other.seq == 1
    assert LOG.head(stream) == 2
    assert LOG.head(other) == 1


def test_entries_are_scoped(stream):
    other = Stream.objects.create(name="scope-b")
    LOG.record(stream, "mine")
    LOG.record(other, "theirs")

    assert [e.event_type for e in LOG.entries(stream)] == ["mine"]


def test_head_of_an_empty_log_is_zero(stream):
    assert LOG.head(stream) == 0
    assert LOG.entries(stream) == []


def test_the_cursor_reads_forward(stream):
    for i in range(5):
        LOG.record(stream, f"e{i}")

    page = LOG.entries(stream, after=2)

    assert [e.seq for e in page] == [3, 4, 5]
    # Caught up: reading from the head returns nothing.
    assert LOG.entries(stream, after=LOG.head(stream)) == []


def test_the_page_limit_is_honored(stream):
    for i in range(5):
        LOG.record(stream, f"e{i}")

    assert [e.seq for e in LOG.entries(stream, after=0, limit=2)] == [1, 2]


def test_a_batch_is_numbered_contiguously(stream):
    first, last = LOG.record_many(stream, [("a", {}), ("b", {}), ("c", {})])

    assert (first, last) == (1, 3)
    assert [e.event_type for e in LOG.entries(stream)] == ["a", "b", "c"]


def test_an_empty_batch_is_a_caller_bug(stream):
    with pytest.raises(ValueError):
        LOG.record_many(stream, [])


def test_append_requests_the_scope_lock(stream, monkeypatch):
    """The lock is the reason this class exists, so it is asserted directly.

    Not via a threaded race: this suite runs on SQLite, where ``select_for_update``
    is a documented no-op and writes serialize at the database anyway — a race test
    here would pass identically with the lock removed, which is worse than no test.
    So assert the lock is *requested*, on the scope row. Proving it actually
    serializes contention belongs to a Postgres-backed suite.
    """
    locked_models = []
    original = models.QuerySet.select_for_update

    def spy(self, *args, **kwargs):
        locked_models.append(self.model)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(models.QuerySet, "select_for_update", spy)

    LOG.record(stream, "thing.happened")

    assert locked_models == [Stream], (
        "record() must lock the scope row before deriving the next seq"
    )


def test_record_joins_an_outer_transaction(stream):
    """Appending inside a caller's atomic block must not commit on its own — a
    rolled-back operation should not leave its audit trail behind."""
    with pytest.raises(RuntimeError), transaction.atomic():
        LOG.record(stream, "will.rollback")
        raise RuntimeError("boom")

    assert LOG.entries(stream) == []
