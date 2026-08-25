"""Infrastructure status collectors for a staff ops dashboard.

**The governing property: an authorized request always gets a 200.** Each section is
collected independently, exception-wrapped, and time-bounded, degrading to
``reachable: false`` or explicit ``None`` rather than hanging or 500ing. That's the
whole point — a status endpoint is most valuable exactly when infrastructure is
broken, which is precisely when a naive implementation blocks on a dead socket and
the dashboard you were going to diagnose the outage with is itself down.

Two details worth keeping when you adapt this:

- **``None`` is not zero.** When the broker is unreachable, worker counts are ``None``,
  not ``0`` — a zero would read as "broker fine, all workers died", which is a
  different incident with a different response.
- **Skip probes whose precondition already failed.** If the broker probe failed,
  ``inspect`` would just burn its full timeout to rediscover that. Cascading the
  known-bad result keeps the endpoint fast under exactly the conditions that matter.

``recent_tasks`` is deliberately not implemented here — task-run history is a
project-owned model. Pass a callable to :func:`collect_status`.

Requires the ``celery`` extra. Usage::

    from drf_foundation.ops_status import collect_status
    from myproject.celery import app

    status = collect_status(app, queue_names=("celery", "priority"))
"""

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from drf_foundation.celery_health import broker_health, get_broker_redis
from drf_foundation.schemas import Schema

if TYPE_CHECKING:
    from celery import Celery

logger = logging.getLogger(__name__)

PROBE_TIMEOUT = 1.0
DEFAULT_QUEUE_NAMES = ("celery",)


class BrokerStatus(Schema):
    reachable: bool
    #: None when the broker itself is unreachable — a count of 0 would misread as
    #: "broker fine, workers gone".
    worker_count: int | None


class QueueDepth(Schema):
    name: str
    #: LLEN of the broker list: tasks queued, excluding what workers have prefetched.
    depth: int


class WorkerCounts(Schema):
    #: All None when the broker is down or inspect timed out — unknown, not zero.
    active: int | None
    reserved: int | None
    scheduled: int | None


class BeatEntry(Schema):
    name: str
    task: str
    #: Stringified schedule (crontab/interval repr) — intent, not runtime state.
    schedule: str


class RedisInfo(Schema):
    reachable: bool
    version: str | None
    used_memory_human: str | None
    connected_clients: int | None
    uptime_days: int | None


class OpsStatus(Schema):
    broker: BrokerStatus
    queues: list[QueueDepth]
    workers: WorkerCounts
    beat: list[BeatEntry]
    redis: RedisInfo


def collect_broker(app: "Celery", timeout: float = PROBE_TIMEOUT) -> BrokerStatus:
    reachable, worker_count = broker_health(app, timeout=timeout)
    return BrokerStatus(reachable=reachable, worker_count=worker_count if reachable else None)


def collect_queues(
    broker_reachable: bool, queue_names: tuple[str, ...] = DEFAULT_QUEUE_NAMES
) -> list[QueueDepth]:
    if not broker_reachable:
        return []
    try:
        client = get_broker_redis()
        return [QueueDepth(name=name, depth=int(client.llen(name))) for name in queue_names]
    except Exception as exc:
        logger.warning("Queue depth probe failed: %s", exc)
        return []


def collect_workers(
    app: "Celery", broker_reachable: bool, timeout: float = PROBE_TIMEOUT
) -> WorkerCounts:
    # Skipped outright when the broker probe already failed: inspect would just burn
    # its full timeout to learn the same thing.
    if not broker_reachable:
        return WorkerCounts(active=None, reserved=None, scheduled=None)
    try:
        inspect = app.control.inspect(timeout=timeout)

        def total(replies: dict[str, list[Any]] | None) -> int:
            return sum(len(tasks) for tasks in (replies or {}).values())

        return WorkerCounts(
            active=total(inspect.active()),
            reserved=total(inspect.reserved()),
            scheduled=total(inspect.scheduled()),
        )
    except Exception as exc:
        logger.warning("Celery inspect failed: %s", exc)
        return WorkerCounts(active=None, reserved=None, scheduled=None)


def collect_beat() -> list[BeatEntry]:
    from django.conf import settings

    schedule = getattr(settings, "CELERY_BEAT_SCHEDULE", {}) or {}
    return [
        BeatEntry(
            name=name,
            task=str(entry.get("task", "")),
            schedule=str(entry.get("schedule", "")),
        )
        for name, entry in sorted(schedule.items())
    ]


def collect_redis() -> RedisInfo:
    try:
        info = get_broker_redis().info()
        return RedisInfo(
            reachable=True,
            version=info.get("redis_version"),
            used_memory_human=info.get("used_memory_human"),
            connected_clients=info.get("connected_clients"),
            uptime_days=info.get("uptime_in_days"),
        )
    except Exception as exc:
        logger.warning("Redis info probe failed: %s", exc)
        return RedisInfo(
            reachable=False,
            version=None,
            used_memory_human=None,
            connected_clients=None,
            uptime_days=None,
        )


def collect_status(
    app: "Celery",
    *,
    queue_names: tuple[str, ...] = DEFAULT_QUEUE_NAMES,
    timeout: float = PROBE_TIMEOUT,
    extra: dict[str, Callable[[], Any]] | None = None,
) -> dict[str, Any]:
    """Every section, as a dict ready to fold into a project's response schema.

    ``extra`` collects project-owned sections (task-run history is the usual one).
    Each callable is exception-wrapped on the same terms as the built-ins, so a broken
    project collector degrades to ``None`` instead of taking the endpoint down with it.
    """
    broker = collect_broker(app, timeout=timeout)
    status: dict[str, Any] = {
        "broker": broker,
        "queues": collect_queues(broker.reachable, queue_names),
        "workers": collect_workers(app, broker.reachable, timeout=timeout),
        "beat": collect_beat(),
        "redis": collect_redis(),
    }
    for name, collect in (extra or {}).items():
        try:
            status[name] = collect()
        except Exception as exc:
            logger.warning("Ops collector %r failed: %s", name, exc)
            status[name] = None
    return status
