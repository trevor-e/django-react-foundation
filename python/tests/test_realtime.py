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


class _FakePubSub:
    """Replays scripted messages, then reports idle (None -> heartbeat) forever."""

    def __init__(self, messages):
        self.messages = list(messages)
        self.channel = None
        self.closed = False

    async def subscribe(self, channel):
        self.channel = channel

    async def get_message(self, ignore_subscribe_messages=True, timeout=None):
        if self.messages:
            return {"data": self.messages.pop(0)}
        return None

    async def aclose(self):
        self.closed = True


class _FakeAsyncRedis:
    last = None

    def __init__(self, messages=()):
        self.ps = _FakePubSub(messages)
        self.closed = False
        _FakeAsyncRedis.last = self

    def pubsub(self):
        return self.ps

    async def aclose(self):
        self.closed = True


def _patch_aioredis(monkeypatch, messages):
    import redis.asyncio as aioredis

    monkeypatch.setattr(
        aioredis.Redis, "from_url", classmethod(lambda cls, url: _FakeAsyncRedis(messages))
    )


async def _consume(response, n):
    """Read n chunks off the streaming response, then close it (client disconnect)."""
    agen = aiter(response.streaming_content)
    chunks = []
    for _ in range(n):
        chunks.append((await anext(agen)).decode())
    await agen.aclose()
    return chunks


def test_sse_relays_messages_and_heartbeats(monkeypatch, db):
    import asyncio

    _patch_aioredis(monkeypatch, [b"1", "2"])
    response = realtime.sse_response("redis://x", "events:h1")
    chunks = asyncio.run(_consume(response, 4))
    assert chunks == [
        "event: connected\ndata: ok\n\n",
        "id: 1\ndata: 1\n\n",
        "id: 2\ndata: 2\n\n",
        ": heartbeat\n\n",
    ]
    fake = _FakeAsyncRedis.last
    assert fake.ps.channel == "events:h1"
    assert fake.ps.closed and fake.closed  # client disconnect cleaned up


def test_sse_lifecycle_hooks_fire(monkeypatch, db):
    import asyncio

    _patch_aioredis(monkeypatch, ["1", "2"])
    calls = {"open": 0, "tick": 0, "close": 0}

    async def on_open():
        calls["open"] += 1

    async def on_tick():
        calls["tick"] += 1

    async def on_close():
        calls["close"] += 1

    response = realtime.sse_response(
        "redis://x", "events:h1", on_open=on_open, on_tick=on_tick, on_close=on_close
    )
    asyncio.run(_consume(response, 4))  # connected + 2 messages + 1 heartbeat
    assert calls == {"open": 1, "tick": 3, "close": 1}


def test_sse_close_hook_fires_when_a_hook_errors(monkeypatch, db):
    import asyncio

    _patch_aioredis(monkeypatch, ["1"])
    calls = {"close": 0}

    async def on_tick():
        raise RuntimeError("hook exploded")

    async def on_close():
        calls["close"] += 1

    response = realtime.sse_response("redis://x", "events:h1", on_tick=on_tick, on_close=on_close)

    async def consume_until_error():
        agen = aiter(response.streaming_content)
        with pytest.raises(RuntimeError):
            while True:
                await anext(agen)

    asyncio.run(consume_until_error())
    assert calls["close"] == 1
    assert _FakeAsyncRedis.last.ps.closed and _FakeAsyncRedis.last.closed


def test_event_stream_renderer_passes_drf_negotiation(db):
    """A DRF-decorated stream view must accept `Accept: text/event-stream` — without
    the renderer, negotiation 406s before the view body runs."""
    from rest_framework.decorators import api_view, renderer_classes
    from rest_framework.test import APIRequestFactory

    from drf_foundation.permissions import public_endpoint

    @api_view(["GET"])
    @public_endpoint
    @renderer_classes([realtime.EventStreamRenderer])
    def stream_view(request):
        return realtime.sse_response("redis://nowhere:1", "events:h1")

    request = APIRequestFactory().get("/stream", HTTP_ACCEPT="text/event-stream")
    response = stream_view(request)
    assert response.status_code == 200
    assert response["Content-Type"] == "text/event-stream"
    response.close()


def test_event_stream_renderer_renders_drf_errors_as_json():
    renderer = realtime.EventStreamRenderer()
    assert renderer.render({"detail": "not found"}) == b'{"detail": "not found"}'
    assert renderer.render("plain") == b"plain"
    assert renderer.render(b"raw") == b"raw"
