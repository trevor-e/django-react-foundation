import logging

import pytest
from asgiref.sync import async_to_sync

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


def test_sse_response_releases_the_db_connection(db, monkeypatch):
    # Endless responses never reach Django's end-of-request cleanup, so the view's
    # DB connection would stay checked out of the pool forever — a handful of open
    # streams then starves every other request (PoolTimeout). sse_response must
    # hand the connection back itself. (Asserted as a spy on close(): the real
    # effect is invisible on test backends — atomic wrappers defer it and
    # in-memory sqlite refuses to close at all.)
    calls: list[bool] = []
    monkeypatch.setattr(realtime.db_connection, "close", lambda: calls.append(True))
    response = realtime.sse_response("redis://nowhere:1", "events:h1")
    assert calls, "sse_response must return the request's DB connection to the pool"
    response.close()


def test_sse_response_from_an_async_view_does_not_500(monkeypatch, db, async_client):
    # The regression: releasing the connection inline raised SynchronousOnlyOperation
    # on the event loop, so an async streaming view 500'd outright. Driven through a
    # real async view, since that is the only shape SSE takes under ASGI.
    _patch_aioredis(monkeypatch, [b"1"])

    response = async_to_sync(async_client.get)("/api/stream")

    assert response.status_code == 200
    assert response["Content-Type"] == "text/event-stream"
    async_to_sync(_drain)(response, 1)


def test_sse_response_refuses_a_running_loop(db):
    # sse_response closes inline, which is only safe off the loop. An async caller
    # that reaches for it gets told which function it wanted, rather than a stream
    # that dies on its first frame.
    async def call_it():
        return realtime.sse_response("redis://x", "events:h1")

    with pytest.raises(RuntimeError, match="asse_response"):
        async_to_sync(call_it)()


def test_async_stream_survives_consumption_outside_the_request_context(
    monkeypatch, db, async_client
):
    # The bug this guards: the connection release used to be deferred into the
    # generator's first step. That step does NOT run inside the request's
    # ThreadSensitiveContext -- ASGIHandler.send_response iterates the streaming
    # content after that context has exited -- so the deferred close landed on a
    # thread that did not own the connection, Django's thread-sharing guard fired,
    # and the stream died before its opening frame with the slot still checked out.
    #
    # Two things let this test see that, and the previous one could see neither:
    # connection.close is NOT monkeypatched (the guard that fires lives inside the
    # real close), and the response is built and consumed in separate async
    # contexts, the way a real ASGI request does.
    _patch_aioredis(monkeypatch, [b"1"])

    response = async_to_sync(async_client.get)("/api/stream")

    chunks = async_to_sync(_drain)(response, 1)

    assert chunks == ["event: connected\ndata: ok\n\n"]


def test_async_stream_releases_the_db_connection(monkeypatch, db, async_client):
    # The release must still happen -- an endless stream that holds its pool slot
    # starves everything else (see sse_response's docstring). Driven through a view
    # that touches the ORM first, because that is what checks a connection out.
    #
    # A spy, for the same reason as the sync test above: the real effect is
    # invisible on test backends (in-memory sqlite refuses to close at all). What
    # is asserted instead is the part that was actually broken -- *which* wrapper
    # gets released. The bug released a fresh wrapper resolved on the wrong thread
    # while the request's own connection stayed checked out.
    import threading

    from django.db import DEFAULT_DB_ALIAS, connections

    from tests.urls import STREAM_DB_CONNECTIONS

    released: list[tuple[int, int]] = []
    real = realtime._close_open_connections

    def spy():
        released.append((threading.get_ident(), id(connections[DEFAULT_DB_ALIAS])))
        real()

    monkeypatch.setattr(realtime, "_close_open_connections", spy)
    _patch_aioredis(monkeypatch, [b"1"])
    STREAM_DB_CONNECTIONS.clear()

    response = async_to_sync(async_client.get)("/api/stream-after-db")
    async_to_sync(_drain)(response, 1)

    assert released, "the stream must hand the request's DB connection back"
    assert STREAM_DB_CONNECTIONS, "the view under test never opened a connection"
    _, released_wrapper = released[0]
    assert released_wrapper == id(STREAM_DB_CONNECTIONS[0]), (
        "the release must target the wrapper the request's ORM work used, not one "
        "resolved fresh on whichever thread the release happened to land on"
    )


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


async def _drain(response, n):
    """Consume the stream in its own async context.

    Deliberately not the context that built the response: ASGIHandler builds inside
    ThreadSensitiveContext and iterates the body after it exits, and that split is
    exactly what the deferred-close bug needed in order to show up.
    """
    return await _consume(response, n)


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
