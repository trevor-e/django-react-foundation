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

import json
import logging
from collections.abc import AsyncIterator, Awaitable, Callable

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
    """

    async def frames() -> AsyncIterator[str]:
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

    response = StreamingHttpResponse(frames(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    # Belt-and-braces for buffering proxies (nginx honors this; harmless elsewhere).
    response["X-Accel-Buffering"] = "no"
    return response
