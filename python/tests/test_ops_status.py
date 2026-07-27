from django.test import override_settings

from drf_foundation import ops_status
from drf_foundation.ops_status import (
    collect_beat,
    collect_queues,
    collect_redis,
    collect_status,
    collect_workers,
)


class FakeInspect:
    def __init__(self, replies=None, raises=False):
        self._replies = replies or {}
        self._raises = raises

    def _get(self, key):
        if self._raises:
            raise ConnectionError("broker gone")
        return self._replies.get(key)

    def active(self):
        return self._get("active")

    def reserved(self):
        return self._get("reserved")

    def scheduled(self):
        return self._get("scheduled")


class FakeApp:
    def __init__(self, inspect=None):
        self._inspect = inspect or FakeInspect()
        self.control = self

    def inspect(self, timeout=None):
        return self._inspect


def test_workers_are_none_not_zero_when_broker_is_down():
    """Zero would read as "broker fine, all workers died" — a different incident."""
    counts = collect_workers(FakeApp(), broker_reachable=False)
    assert (counts.active, counts.reserved, counts.scheduled) == (None, None, None)


def test_worker_inspect_is_skipped_entirely_when_broker_is_down():
    """Not just ignored — never called, or it burns its full timeout rediscovering
    what the broker probe already established."""
    inspect = FakeInspect(raises=True)

    class Exploding(FakeApp):
        def inspect(self, timeout=None):
            raise AssertionError("inspect must not run when the broker is unreachable")

    assert collect_workers(Exploding(inspect), broker_reachable=False).active is None


def test_worker_counts_sum_across_workers():
    replies = {"active": {"w1": [1, 2], "w2": [3]}, "reserved": {"w1": []}, "scheduled": None}
    app = FakeApp(FakeInspect(replies))
    counts = collect_workers(app, broker_reachable=True)
    assert (counts.active, counts.reserved, counts.scheduled) == (3, 0, 0)


def test_inspect_failure_degrades_to_unknown():
    counts = collect_workers(FakeApp(FakeInspect(raises=True)), broker_reachable=True)
    assert counts.active is None


def test_queues_are_empty_when_broker_is_down():
    assert collect_queues(broker_reachable=False) == []


def test_queue_probe_failure_degrades_to_empty(monkeypatch):
    monkeypatch.setattr(
        ops_status, "get_broker_redis", lambda: (_ for _ in ()).throw(ConnectionError("no"))
    )
    assert collect_queues(broker_reachable=True) == []


def test_queue_depths_are_read_per_name(monkeypatch):
    class FakeRedis:
        def llen(self, name):
            return {"celery": 4, "priority": 0}[name]

    monkeypatch.setattr(ops_status, "get_broker_redis", lambda: FakeRedis())
    depths = collect_queues(True, queue_names=("celery", "priority"))
    assert [(d.name, d.depth) for d in depths] == [("celery", 4), ("priority", 0)]


def test_redis_info_failure_degrades_rather_than_raising(monkeypatch):
    monkeypatch.setattr(
        ops_status, "get_broker_redis", lambda: (_ for _ in ()).throw(ConnectionError("no"))
    )
    info = collect_redis()
    assert info.reachable is False
    assert info.version is None


def test_redis_info_is_read_when_reachable(monkeypatch):
    class FakeRedis:
        def info(self):
            return {"redis_version": "7.2.0", "used_memory_human": "1.2M", "uptime_in_days": 3}

    monkeypatch.setattr(ops_status, "get_broker_redis", lambda: FakeRedis())
    info = collect_redis()
    assert (info.reachable, info.version, info.uptime_days) == (True, "7.2.0", 3)


@override_settings(CELERY_BEAT_SCHEDULE={"b": {"task": "t.b"}, "a": {"task": "t.a"}})
def test_beat_entries_are_sorted_by_name():
    assert [e.name for e in collect_beat()] == ["a", "b"]


def test_beat_tolerates_no_schedule_configured():
    with override_settings(CELERY_BEAT_SCHEDULE={}):
        assert collect_beat() == []


def test_a_broken_project_collector_cannot_take_the_endpoint_down(monkeypatch):
    """The governing property: an authorized request always gets a 200."""
    monkeypatch.setattr(ops_status, "broker_health", lambda app, timeout=None: (False, 0))
    monkeypatch.setattr(
        ops_status, "get_broker_redis", lambda: (_ for _ in ()).throw(ConnectionError("no"))
    )
    status = collect_status(
        FakeApp(), extra={"recent_tasks": lambda: (_ for _ in ()).throw(RuntimeError("db down"))}
    )
    assert status["recent_tasks"] is None
    assert status["broker"].reachable is False


def test_extra_collectors_are_included(monkeypatch):
    monkeypatch.setattr(ops_status, "broker_health", lambda app, timeout=None: (False, 0))
    monkeypatch.setattr(
        ops_status, "get_broker_redis", lambda: (_ for _ in ()).throw(ConnectionError("no"))
    )
    status = collect_status(FakeApp(), extra={"recent_tasks": lambda: ["a", "b"]})
    assert status["recent_tasks"] == ["a", "b"]


def test_collect_status_never_raises_with_everything_down(monkeypatch):
    monkeypatch.setattr(ops_status, "broker_health", lambda app, timeout=None: (False, 0))
    monkeypatch.setattr(
        ops_status, "get_broker_redis", lambda: (_ for _ in ()).throw(ConnectionError("no"))
    )
    status = collect_status(FakeApp())
    assert set(status) == {"broker", "queues", "workers", "beat", "redis"}
