"""Best-effort presence derived from SSE stream lifecycles.

One Redis key per ``(group, member)`` holds that member's open-connection count with a
TTL: first connection flips them online, last disconnect flips them offline, extra tabs
neither. Heartbeats refresh the TTL so a member on a long-lived stream stays present;
a process that dies without disconnecting stops counting once the TTL lapses.

Semantics are display-grade, deliberately: a crash that skips ``disconnect`` inflates
the count until the TTL self-heals it away, and every operation here is fail-soft (a
Redis outage degrades presence to "offline", never breaks the stream or the caller;
registration is a single MULTI/EXEC so the key can never land without a TTL). Nothing
authoritative may depend on presence — use it for green dots and "opponent is online"
copy, and let readers re-check :func:`is_present` for truth-at-load.

Intended wiring — ride ``sse_response``'s lifecycle hooks:

    tracker = PresenceTracker(redis_url, group=f"war:{war.id}", member=str(seat),
                              on_flip=my_flip_handler)
    return sse_response(redis_url, channel,
                        on_open=tracker.connect,
                        on_tick=tracker.heartbeat,
                        on_close=tracker.disconnect)
"""

import contextlib
import logging
import time
from collections.abc import Awaitable, Callable

from drf_foundation.realtime import _sync_client

log = logging.getLogger(__name__)


def presence_key(group: str, member: str) -> str:
    return f"presence:{group}:{member}"


class PresenceTracker:
    """Tracks one connection's contribution to one member's presence on a group.

    Create one tracker per stream/connection. ``on_flip(online)`` fires only on the
    offline→online and online→offline transitions (awaited inline — keep it cheap).
    ``client`` injects a pre-built ``redis.asyncio`` client (tests, connection reuse);
    by default one is created lazily from ``redis_url``.
    """

    def __init__(
        self,
        redis_url: str,
        group: str,
        member: str,
        *,
        ttl_seconds: float = 90.0,
        on_flip: Callable[[bool], Awaitable[None]] | None = None,
        client: object | None = None,
    ) -> None:
        self._redis_url = redis_url
        self.key = presence_key(group, member)
        self._ttl = max(1, int(ttl_seconds))
        self._on_flip = on_flip
        self._client = client
        self._connected = False
        self._owns_client = client is None
        self._last_refresh = 0.0

    def _redis(self) -> object:
        if self._client is None:
            import redis.asyncio as aioredis

            self._client = aioredis.Redis.from_url(
                self._redis_url, socket_connect_timeout=1, socket_timeout=1
            )
        return self._client

    async def _flip(self, online: bool) -> None:
        if self._on_flip is not None:
            await self._on_flip(online)

    async def _register(self) -> int:
        """INCR + EXPIRE in one MULTI/EXEC. Atomicity is load-bearing: an INCR that
        landed without its EXPIRE would leave a TTL-less key no crash ever heals —
        the member would read as present forever."""
        r = self._redis()
        pipe = r.pipeline(transaction=True)  # pyright: ignore[reportAttributeAccessIssue]
        pipe.incr(self.key)
        pipe.expire(self.key, self._ttl)
        count, _ = await pipe.execute()
        return count

    async def connect(self) -> None:
        """Count this connection in; flips the member online if it's their first."""
        try:
            count = await self._register()
            self._connected = True
            self._last_refresh = time.monotonic()
            if count == 1:
                await self._flip(True)
        except Exception:
            log.warning("presence connect failed for %s", self.key, exc_info=True)

    async def heartbeat(self) -> None:
        """Refresh the TTL (throttled to every ttl/3). Wire to ``on_tick`` — busy
        streams never idle, so heartbeat-only refresh would lapse a present member.

        If the key expired underneath us (long GC pause, Redis restart), this
        connection re-registers and re-flips online so observers reconverge."""
        if not self._connected:
            return
        now = time.monotonic()
        if now - self._last_refresh < self._ttl / 3:
            return
        try:
            r = self._redis()
            refreshed = await r.expire(self.key, self._ttl)  # pyright: ignore[reportAttributeAccessIssue]
            self._last_refresh = now
            if not refreshed:
                count = await self._register()
                if count == 1:
                    await self._flip(True)
        except Exception:
            log.warning("presence heartbeat failed for %s", self.key, exc_info=True)

    async def disconnect(self) -> None:
        """Count this connection out; flips the member offline if it was their last.

        Always closes a lazily created client — a failed :meth:`connect` builds one
        without ever counting the connection in, and it must not leak."""
        try:
            if self._connected:
                self._connected = False
                r = self._redis()
                count = await r.decr(self.key)  # pyright: ignore[reportAttributeAccessIssue]
                if count <= 0:
                    await r.delete(self.key)  # pyright: ignore[reportAttributeAccessIssue]
                    await self._flip(False)
        except Exception:
            log.warning("presence disconnect failed for %s", self.key, exc_info=True)
        finally:
            if self._owns_client and self._client is not None:
                with contextlib.suppress(Exception):  # best-effort close
                    await self._client.aclose()  # pyright: ignore[reportAttributeAccessIssue]
                self._client = None


def is_present(redis_url: str, group: str, member: str) -> bool:
    """Synchronous presence read for views and background jobs. Fail-soft: an
    unreachable store reads as "not present" so absence-driven fallbacks (send the
    email, show offline) engage rather than raise."""
    try:
        client = _sync_client(redis_url)
        raw = client.get(presence_key(group, member))  # pyright: ignore[reportAttributeAccessIssue]
        return raw is not None and int(raw) > 0
    except Exception:
        log.warning("presence read failed for %s:%s", group, member, exc_info=True)
        return False
