"""Redis-pub/sub realtime plumbing: fail-soft publish + an SSE streaming response.

The pattern (blueprint §11a's companion): route every notable domain action through
one recording choke point, publish each change's id to a per-tenant channel there,
and stream those ids to browsers over SSE. Clients react by invalidating their query
cache and refetching — the server stays the single source of truth; no payloads ride
the stream, so its wire format never constrains the domain model. Pair with
`readEventStream`/`createRealtimeSync` from the JS package.

Requires the `realtime` extra (`django-drf-foundation[realtime]`) for redis-py.
The SSE response must be served under ASGI (blueprint §11a) — the generator is async
and would pin a whole thread per client on WSGI.
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

from asgiref.sync import sync_to_async
from django.db import connection as db_connection
from django.db import connections
from django.http import StreamingHttpResponse
from rest_framework.renderers import BaseRenderer

log = logging.getLogger(__name__)


class EventStreamRenderer(BaseRenderer):
    """Satisfies DRF content negotiation for SSE endpoints.

    A DRF-decorated view 406s ``Accept: text/event-stream`` before the view body
    ever runs — DRF's default renderers only speak JSON. Declare this on stream
    views::

        @api_view(["GET"])
        @renderer_classes([EventStreamRenderer])
        def my_stream(request): ...
        return sse_response(...)

    The happy path never renders (``sse_response`` returns a ``StreamingHttpResponse``,
    which DRF passes through untouched); ``render`` only runs for pre-stream DRF
    errors (401/403/404), which it emits as JSON bytes so error bodies stay readable
    to the reconnecting client.
    """

    media_type = "text/event-stream"
    format = "event-stream"
    charset = None
    render_style = "binary"

    def render(self, data: object, accepted_media_type=None, renderer_context=None) -> bytes:  # noqa: ANN001
        if isinstance(data, bytes):
            return data
        if isinstance(data, str):
            return data.encode()
        return json.dumps(data).encode()


# Lazy singleton per URL; redis-py clients are thread-safe and reconnect per command,
# so one client serves web threads and Celery workers alike.
_clients: dict[str, object] = {}


def _sync_client(redis_url: str) -> object:
    """The cached synchronous redis client for ``redis_url`` (bounded socket timeouts
    so a dead route fails fast — blueprint §1c). Shared by :func:`publish` and the
    presence module's sync reads."""
    import redis

    client = _clients.get(redis_url)
    if client is None:
        client = redis.Redis.from_url(redis_url, socket_connect_timeout=1, socket_timeout=1)
        _clients[redis_url] = client
    return client


def publish(redis_url: str, channel: str, message: str) -> None:
    """Publish fail-soft: never raises — a Redis outage must not break the write path
    it piggybacks on (subscribers degrade to their polling fallback instead)."""
    try:
        client = _sync_client(redis_url)
        client.publish(channel, message)  # pyright: ignore[reportAttributeAccessIssue]
    except Exception:
        log.warning("realtime publish failed for channel %s", channel, exc_info=True)


type _Hook = Callable[[], Awaitable[None]] | None


async def _frames(
    redis_url: str,
    channel: str,
    heartbeat_seconds: float,
    *,
    on_open: _Hook,
    on_tick: _Hook,
    on_close: _Hook,
) -> AsyncIterator[str]:
    import redis.asyncio as aioredis

    client = aioredis.Redis.from_url(redis_url)
    pubsub = client.pubsub()
    try:
        await pubsub.subscribe(channel)
        if on_open is not None:
            await on_open()
        # Named event so clients can distinguish the open from data frames.
        yield "event: connected\ndata: ok\n\n"
        while True:
            message = await pubsub.get_message(
                ignore_subscribe_messages=True, timeout=heartbeat_seconds
            )
            # Tick before the yield: the tick belongs to the iteration that
            # produced this frame, not to whenever the consumer pulls the next one.
            if on_tick is not None:
                await on_tick()
            if message is None:
                yield ": heartbeat\n\n"
            else:
                data = message["data"]
                text = data.decode() if isinstance(data, bytes) else str(data)
                yield f"id: {text}\ndata: {text}\n\n"
    finally:
        try:
            if on_close is not None:
                await on_close()
        finally:
            await pubsub.aclose()
            await client.aclose()


def sse_response(
    redis_url: str,
    channel: str,
    heartbeat_seconds: float = 25.0,
    *,
    on_open: _Hook = None,
    on_tick: _Hook = None,
    on_close: _Hook = None,
) -> StreamingHttpResponse:
    """A ``text/event-stream`` response relaying the channel's messages as SSE frames.

    Each pub/sub message becomes ``id: <msg>\\ndata: <msg>\\n\\n``; a named
    ``connected`` event opens the stream, and comment heartbeats flow every
    ``heartbeat_seconds`` so proxy idle timeouts (Cloudflare cuts idle connections at
    ~100s) never fire. Auth/tenancy is the caller's job — resolve and reject *before*
    constructing this response.

    Optional per-connection lifecycle hooks (async callables, awaited inline — keep
    them cheap): ``on_open`` fires once when the stream starts consuming, ``on_tick``
    on every relay iteration (message *or* idle heartbeat — a busy channel never
    idles, so TTL-refreshing concerns must hook ticks, not heartbeats), ``on_close``
    exactly once when the stream ends for any reason, including client disconnect and
    errors. The presence module (``drf_foundation.presence``) is the intended rider.

    Serving note: streams never finish, so granian needs ``--workers-kill-timeout``
    or every graceful stop wedges on the first connected client (blueprint §11a).

    Because the stream never finishes, Django's end-of-request cleanup never runs
    for it either — whatever pooled DB connection the view's auth/tenancy work
    used would stay checked out for the connection's whole life. A handful of
    open streams would then exhaust a small pool (§1b bounds default to 5) and
    starve every other request into ``PoolTimeout``. This function therefore
    returns the request's DB connection to the pool itself — do all DB work
    before calling it.

    This is the **sync-caller** entry point, and it closes inline. An async view
    cannot: ``connection.close()`` is ``@async_unsafe`` and raises on the event
    loop. Those callers use ``asse_response``, which awaits the release on the
    thread that owns the connection — see that function for why the release cannot
    simply be deferred into the generator instead. Calling this one with a running
    loop raises rather than guessing.
    """

    # See the docstring: endless responses must not pin pool slots. A sync caller
    # hands the connection back right now; an async one must await asse_response,
    # because the release has to happen while the request's ThreadSensitiveContext
    # is still open (below).
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError(
            "sse_response() was called with a running event loop. Use "
            "`await asse_response(...)` from an async view: the DB connection can "
            "only be released on the thread that owns it, and that routing exists "
            "just while the view runs."
        )
    db_connection.close()

    return _stream(
        _frames(
            redis_url,
            channel,
            heartbeat_seconds,
            on_open=on_open,
            on_tick=on_tick,
            on_close=on_close,
        )
    )


def _stream(frames: AsyncIterator[str]) -> StreamingHttpResponse:
    response = StreamingHttpResponse(frames, content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    # Belt-and-braces for buffering proxies (nginx honors this; harmless elsewhere).
    response["X-Accel-Buffering"] = "no"
    return response


def _close_open_connections() -> None:
    """Close whatever connections *this thread* has open. Resolve, don't receive.

    ``django.db.connections`` is an asgiref ``Local``: handing a wrapper across a
    thread boundary carries the caller's wrapper along, so it arrives on a thread
    that does not own it and Django's thread-sharing guard raises. Resolving here,
    on the thread that will do the closing, sidesteps that entirely — the wrapper
    this finds is the one this thread opened.
    """
    for conn in connections.all(initialized_only=True):
        conn.close()


async def _arelease_connection() -> None:
    """Hand this request's DB connection back to the pool, from an async view.

    ``connection.close()`` is ``@async_unsafe`` and cannot run inline on the loop,
    so the release is routed to the thread-sensitive executor — the same thread the
    sync middleware ran on, and therefore the one holding the connection the request
    checked out. ``thread_sensitive=True`` only guarantees that while the request's
    ``ThreadSensitiveContext`` is open, which is why this is awaited from the view
    rather than deferred into the stream generator.
    """
    await sync_to_async(_close_open_connections, thread_sensitive=True)()


async def asse_response(
    redis_url: str,
    channel: str,
    heartbeat_seconds: float = 25.0,
    *,
    on_open: _Hook = None,
    on_tick: _Hook = None,
    on_close: _Hook = None,
) -> StreamingHttpResponse:
    """`sse_response` for async views — the shape SSE actually takes under ASGI.

    Same response, but the DB connection is released here, awaited from the view,
    rather than deferred into the stream generator.

    That distinction is the whole reason this function exists. ``connection.close()``
    is ``@async_unsafe`` and cannot run inline on the loop, so the release has to be
    routed to the thread holding the connection via ``thread_sensitive=True``. That
    routing is only correct *inside the request's* ``ThreadSensitiveContext`` — the
    one Django opens around ``run_get_response``, which is where the view (and the
    sync middleware that opened the connection) run. Deferring the release into the
    generator moves it out of that context: ``ASGIHandler.send_response`` iterates
    the streaming content **after** the context has exited, so asgiref allocates a
    fresh executor thread, Django's thread-sharing guard fires, and the stream dies
    on its first step — before the opening frame, with the connection still checked
    out. Awaiting it here keeps the release on the owning thread.
    """
    await _arelease_connection()

    return _stream(
        _frames(
            redis_url,
            channel,
            heartbeat_seconds,
            on_open=on_open,
            on_tick=on_tick,
            on_close=on_close,
        )
    )
