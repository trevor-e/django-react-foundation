"""Celery/Redis broker health check for an admin dashboard or health endpoint.

Two-stage probe so a dashboard fails fast and never hangs:
  1. redis-py ``PING`` — is the broker reachable at all? (sub-ms when healthy)
  2. Celery control ping — how many workers answer? Only attempted once the
     broker is known-reachable, with a short timeout.

**Gotcha this avoids:** don't count workers via ``PUBSUB CHANNELS`` — Kombu
implements the pidbox control mailbox with pattern subscriptions (``PSUBSCRIBE``),
which ``PUBSUB CHANNELS`` never returns, so that approach always reports 0
workers even with a healthy worker attached. Use ``app.control.ping()`` instead.
"""

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    # Optional deps (see the package's `celery` extra) — only needed for typing.
    import redis
    from celery import Celery

logger = logging.getLogger(__name__)

_broker_redis: "redis.Redis | None" = None


def get_broker_redis() -> "redis.Redis":
    """A cached, short-timeout redis client for health probes (not app work).

    Reads the broker URL from ``settings.CELERY_BROKER_URL``.
    """
    global _broker_redis
    if _broker_redis is None:
        import redis
        from django.conf import settings

        _broker_redis = redis.from_url(
            settings.CELERY_BROKER_URL,
            socket_connect_timeout=5.0,
            socket_timeout=5.0,
            socket_keepalive=True,
        )
    return _broker_redis


def broker_health(app: "Celery", timeout: float = 1.0) -> tuple[bool, int]:
    """``(broker_reachable, worker_count)`` for the given Celery ``app``.

    Pass your project's own Celery app instance (e.g. ``from myproject.celery
    import app``) — this function doesn't import or discover one for you.
    """
    try:
        get_broker_redis().ping()
    except Exception as e:
        logger.warning(f"Broker health check failed: {e}")
        return False, 0

    try:
        replies = app.control.ping(timeout=timeout) or []
        return True, len(replies)
    except Exception as e:
        logger.warning(f"Worker control ping failed: {e}")
        return True, 0
