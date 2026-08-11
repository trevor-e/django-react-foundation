import logging

import pytest
from django.db import IntegrityError, transaction
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from drf_foundation import event_log, realtime
from drf_foundation.schemas import RequestValidationError
from tests.testapp.models import Stream, StreamEvent

pytestmark = pytest.mark.django_db


class _FakeRedis:
    def __init__(self, fail=False):
        self.fail = fail
        self.published = []

    def publish(self, channel, message):
        if self.fail:
            raise ConnectionError("redis down")
        self.published.append((channel, message))


@pytest.fixture(autouse=True)
def _clean_clients():
    realtime._clients.clear()
    yield
    realtime._clients.clear()


@pytest.fixture
def stream():
    return Stream.objects.create(name="s1")


def _drf_request(query: dict) -> Request:
    return Request(APIRequestFactory().get("/", query))


# --- append_events / head_seq ---


def test_first_append_starts_at_one(stream):
    first, last = event_log.append_events(
        stream.events.all(),
        [("created", {"a": 1}), ("renamed", {"b": 2}), ("closed", {})],
        extra_fields={"stream": stream},
    )
    assert (first, last) == (1, 3)
    rows = list(stream.events.all())
    assert [r.seq for r in rows] == [1, 2, 3]
    assert [r.event_type for r in rows] == ["created", "renamed", "closed"]
    assert rows[0].payload == {"a": 1}


def test_append_continues_from_head(stream):
    event_log.append_events(
        stream.events.all(), [("a", {}), ("b", {}), ("c", {})], extra_fields={"stream": stream}
    )
    first, last = event_log.append_events(
        stream.events.all(), [("d", {}), ("e", {})], extra_fields={"stream": stream}
    )
    assert (first, last) == (4, 5)


def test_scopes_are_independent(stream):
    other = Stream.objects.create(name="s2")
    event_log.append_events(stream.events.all(), [("a", {})], extra_fields={"stream": stream})
    first, last = event_log.append_events(
        other.events.all(), [("a", {})], extra_fields={"stream": other}
    )
    assert (first, last) == (1, 1)


def test_empty_batch_is_a_caller_bug(stream):
    with pytest.raises(ValueError):
        event_log.append_events(stream.events.all(), [], extra_fields={"stream": stream})


def test_head_seq_of_empty_scope_is_zero(stream):
    assert event_log.head_seq(stream.events.all()) == 0


def test_unique_constraint_backstops_seq_collisions(stream):
    """Two writers violating the lock protocol must surface as an IntegrityError,
    never as silent duplicate seqs."""
    event_log.append_events(stream.events.all(), [("a", {})], extra_fields={"stream": stream})
    with pytest.raises(IntegrityError), transaction.atomic():
        StreamEvent.objects.create(stream=stream, seq=1, event_type="dupe", payload={})


# --- events_after ---


def test_events_after_orders_and_caps(stream):
    event_log.append_events(
        stream.events.all(),
        [(f"e{i}", {"i": i}) for i in range(1, 11)],
        extra_fields={"stream": stream},
    )
    page = event_log.events_after(stream.events.all(), 4, limit=4)
    assert [e.seq for e in page] == [5, 6, 7, 8]


def test_events_after_caught_up_is_empty(stream):
    event_log.append_events(stream.events.all(), [("a", {})], extra_fields={"stream": stream})
    assert event_log.events_after(stream.events.all(), 1) == []


# --- after_param ---


def test_after_param_defaults_to_zero():
    assert event_log.after_param(_drf_request({})) == 0
    assert event_log.after_param(_drf_request({"after": ""})) == 0


def test_after_param_parses_integers():
    assert event_log.after_param(_drf_request({"after": "17"})) == 17


@pytest.mark.parametrize("bad", ["banana", "-3", "1.5"])
def test_after_param_rejects_malformed(bad):
    with pytest.raises(RequestValidationError):
        event_log.after_param(_drf_request({"after": bad}))


# --- publish_after_commit ---


def test_publish_rides_the_commit(stream, django_capture_on_commit_callbacks):
    fake = _FakeRedis()
    realtime._clients["redis://x"] = fake
    with django_capture_on_commit_callbacks(execute=True) as callbacks:
        event_log.publish_after_commit("redis://x", "stream:1", "5")
        assert fake.published == []  # nothing before commit
    assert len(callbacks) == 1
    assert fake.published == [("stream:1", "5")]


def test_rollback_publishes_nothing(stream, django_capture_on_commit_callbacks):
    fake = _FakeRedis()
    realtime._clients["redis://x"] = fake
    with (
        django_capture_on_commit_callbacks(execute=True) as callbacks,
        pytest.raises(RuntimeError),
        transaction.atomic(),
    ):
        event_log.publish_after_commit("redis://x", "stream:1", "5")
        raise RuntimeError("abort")
    assert callbacks == []
    assert fake.published == []


def test_publish_after_commit_is_fail_soft(stream, django_capture_on_commit_callbacks, caplog):
    realtime._clients["redis://x"] = _FakeRedis(fail=True)
    with caplog.at_level(logging.WARNING), django_capture_on_commit_callbacks(execute=True):
        event_log.publish_after_commit("redis://x", "stream:1", "5")  # must not raise
    assert "realtime publish failed" in caplog.text
