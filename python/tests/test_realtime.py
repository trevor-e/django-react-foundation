import logging

import pytest

from drf_foundation import realtime


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


def test_publish_sends_to_channel():
    fake = _FakeRedis()
    realtime._clients["redis://x"] = fake
    realtime.publish("redis://x", "events:h1", "event_123")
    assert fake.published == [("events:h1", "event_123")]


def test_publish_is_fail_soft(caplog):
    realtime._clients["redis://x"] = _FakeRedis(fail=True)
    with caplog.at_level(logging.WARNING):
        realtime.publish("redis://x", "events:h1", "event_123")  # must not raise
    assert "realtime publish failed" in caplog.text


def test_publish_client_is_cached_per_url():
    fake = _FakeRedis()
    realtime._clients["redis://x"] = fake
    realtime.publish("redis://x", "a", "1")
    realtime.publish("redis://x", "b", "2")
    assert realtime._clients == {"redis://x": fake}
    assert len(fake.published) == 2


def test_sse_response_shape(db):
    # Constructing the response must not touch Redis — the generator only runs when
    # consumed, so auth-rejection paths and tests never open a connection.
    response = realtime.sse_response("redis://nowhere:1", "events:h1")
    assert response["Content-Type"] == "text/event-stream"
    assert response["Cache-Control"] == "no-cache"
    assert response["X-Accel-Buffering"] == "no"
    assert response.streaming
    response.close()
