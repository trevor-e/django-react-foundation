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

import logging
from collections.abc import AsyncIterator

from django.http import StreamingHttpResponse

log = logging.getLogger(__name__)

# Lazy singleton per URL; redis-py clients are thread-safe and reconnect per command,
# so one client serves web threads and Celery workers alike.
_clients: dict[str, object] = {}


def publish(redis_url: str, channel: str, message: str) -> None:
    """Publish fail-soft: never raises — a Redis outage must not break the write path
    it piggybacks on (subscribers degrade to their polling fallback instead)."""
    import redis

    try:
        client = _clients.get(redis_url)
        if client is None:
            client = redis.Redis.from_url(
                redis_url, socket_connect_timeout=1, socket_timeout=1
            )
            _clients[redis_url] = client
        client.publish(channel, message)  # pyright: ignore[reportAttributeAccessIssue]
    except Exception:
        log.warning("realtime publish failed for channel %s", channel, exc_info=True)


def sse_response(
    redis_url: str, channel: str, heartbeat_seconds: float = 25.0
) -> StreamingHttpResponse:
    """A ``text/event-stream`` response relaying the channel's messages as SSE frames.

    Each pub/sub message becomes ``id: <msg>\\ndata: <msg>\\n\\n``; a named
    ``connected`` event opens the stream, and comment heartbeats flow every
    ``heartbeat_seconds`` so proxy idle timeouts (Cloudflare cuts idle connections at
    ~100s) never fire. Auth/tenancy is the caller's job — resolve and reject *before*
    constructing this response.

    Serving note: streams never finish, so granian needs ``--workers-kill-timeout``
    or every graceful stop wedges on the first connected client (blueprint §11a).
    """

    async def frames() -> AsyncIterator[str]:
        import redis.asyncio as aioredis

        client = aioredis.Redis.from_url(redis_url)
        pubsub = client.pubsub()
        try:
            await pubsub.subscribe(channel)
            # Named event so clients can distinguish the open from data frames.
            yield "event: connected\ndata: ok\n\n"
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True, timeout=heartbeat_seconds
                )
                if message is None:
                    yield ": heartbeat\n\n"
                    continue
                data = message["data"]
                text = data.decode() if isinstance(data, bytes) else str(data)
                yield f"id: {text}\ndata: {text}\n\n"
        finally:
            await pubsub.aclose()
            await client.aclose()

    response = StreamingHttpResponse(frames(), content_type="text/event-stream")
    response["Cache-Control"] = "no-cache"
    # Belt-and-braces for buffering proxies (nginx honors this; harmless elsewhere).
    response["X-Accel-Buffering"] = "no"
    return response
