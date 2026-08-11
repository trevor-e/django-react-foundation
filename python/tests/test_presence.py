import asyncio
import logging

import pytest

from drf_foundation import presence, realtime


class _FakePipeline:
    """Queues incr/expire like redis-py's pipeline and applies them on execute()."""

    def __init__(self, client):
        self._client = client
        self._ops = []

    def incr(self, key):
        self._ops.append(("incr", key))
        return self

    def expire(self, key, ttl):
        self._ops.append(("expire", key, ttl))
        return self

    async def execute(self):
        results = []
        for op, *args in self._ops:
            results.append(await getattr(self._client, op)(*args))
        self._ops = []
        return results


class _FakeAioRedis:
    """In-memory async redis: enough surface for PresenceTracker (incr/decr/expire/
    delete/pipeline) plus test helpers to simulate TTL lapse and count calls."""

    def __init__(self):
        self.store = {}
        self.ttls = {}
        self.expire_calls = 0
        self.closed = False

    def pipeline(self, transaction=True):
        return _FakePipeline(self)

    async def incr(self, key):
        self.store[key] = int(self.store.get(key, 0)) + 1
        return self.store[key]

    async def decr(self, key):
        self.store[key] = int(self.store.get(key, 0)) - 1
        return self.store[key]

    async def expire(self, key, ttl):
        self.expire_calls += 1
        if key in self.store:
            self.ttls[key] = ttl
            return True
        return False

    async def delete(self, key):
        self.store.pop(key, None)
        self.ttls.pop(key, None)

    async def aclose(self):
        self.closed = True

    def lapse(self, key):
        """Simulate the TTL expiring: the key vanishes."""
        self.store.pop(key, None)
        self.ttls.pop(key, None)


class _FakeSyncRedis:
    def __init__(self, value=None, fail=False):
        self.value = value
        self.fail = fail

    def get(self, key):
        if self.fail:
            raise ConnectionError("redis down")
        return self.value


@pytest.fixture(autouse=True)
def _clean_clients():
    realtime._clients.clear()
    yield
    realtime._clients.clear()


def _tracker(client, flips, member="0", ttl=90.0):
    async def on_flip(online):
        flips.append(online)

    return presence.PresenceTracker(
        "redis://x", "war:1", member, ttl_seconds=ttl, on_flip=on_flip, client=client
    )


def test_first_connection_flips_online_extra_tabs_do_not():
    client = _FakeAioRedis()
    flips = []
    tab1, tab2 = _tracker(client, flips), _tracker(client, flips)

    async def scenario():
        await tab1.connect()
        await tab2.connect()
        await tab1.disconnect()

    asyncio.run(scenario())
    assert flips == [True]  # one online flip, no offline yet
    assert client.store[presence.presence_key("war:1", "0")] == 1


def test_last_disconnect_flips_offline_and_clears_the_key():
    client = _FakeAioRedis()
    flips = []
    tab1, tab2 = _tracker(client, flips), _tracker(client, flips)

    async def scenario():
        await tab1.connect()
        await tab2.connect()
        await tab1.disconnect()
        await tab2.disconnect()

    asyncio.run(scenario())
    assert flips == [True, False]
    assert presence.presence_key("war:1", "0") not in client.store


def test_double_disconnect_is_inert():
    client = _FakeAioRedis()
    flips = []
    tab = _tracker(client, flips)

    async def scenario():
        await tab.connect()
        await tab.disconnect()
        await tab.disconnect()  # second is a no-op

    asyncio.run(scenario())
    assert flips == [True, False]


def test_heartbeat_refreshes_ttl_and_throttles():
    client = _FakeAioRedis()
    tab = _tracker(client, [], ttl=90.0)

    async def scenario():
        await tab.connect()
        first_calls = client.expire_calls
        await tab.heartbeat()  # inside ttl/3 window -> throttled away
        assert client.expire_calls == first_calls
        tab._last_refresh = 0.0  # step past the throttle window
        await tab.heartbeat()
        assert client.expire_calls == first_calls + 1

    asyncio.run(scenario())
    assert client.ttls[presence.presence_key("war:1", "0")] == 90


def test_heartbeat_reregisters_after_ttl_lapse():
    client = _FakeAioRedis()
    flips = []
    tab = _tracker(client, flips)

    async def scenario():
        await tab.connect()
        client.lapse(presence.presence_key("war:1", "0"))
        tab._last_refresh = 0.0
        await tab.heartbeat()

    asyncio.run(scenario())
    assert flips == [True, True]  # came back online after the lapse
    assert client.store[presence.presence_key("war:1", "0")] == 1


def test_tracker_is_fail_soft(caplog):
    class _Exploding:
        def __getattr__(self, name):
            # Raises at call time (not await time) so sync surface like pipeline()
            # explodes too, without leaving never-awaited coroutines behind.
            def boom(*args, **kwargs):
                raise ConnectionError("redis down")

            return boom

    flips = []
    tab = _tracker(_Exploding(), flips)

    async def scenario():
        await tab.connect()  # must not raise
        # a failed connect never counted this connection, so disconnect is a no-op
        await tab.disconnect()

    with caplog.at_level(logging.WARNING):
        asyncio.run(scenario())
    assert flips == []
    assert "presence connect failed" in caplog.text


def test_failed_connect_still_closes_the_owned_client(monkeypatch):
    """A connect that fails after lazily building its client must not leak it — the
    stream's on_close still runs disconnect, which owns the cleanup."""
    import redis.asyncio as aioredis

    class _RegisterExploding(_FakeAioRedis):
        def pipeline(self, transaction=True):
            raise ConnectionError("redis down")

    fake = _RegisterExploding()
    monkeypatch.setattr(aioredis.Redis, "from_url", classmethod(lambda cls, url, **kw: fake))
    tab = presence.PresenceTracker("redis://x", "war:1", "0")

    async def scenario():
        await tab.connect()  # builds the client, then fails to register
        await tab.disconnect()

    asyncio.run(scenario())
    assert fake.closed


def test_is_present_reads_the_count():
    realtime._clients["redis://x"] = _FakeSyncRedis(value=b"2")
    assert presence.is_present("redis://x", "war:1", "0") is True
    realtime._clients["redis://x"] = _FakeSyncRedis(value=b"0")
    assert presence.is_present("redis://x", "war:1", "0") is False
    realtime._clients["redis://x"] = _FakeSyncRedis(value=None)
    assert presence.is_present("redis://x", "war:1", "0") is False


def test_is_present_store_outage_reads_offline(caplog):
    realtime._clients["redis://x"] = _FakeSyncRedis(fail=True)
    with caplog.at_level(logging.WARNING):
        assert presence.is_present("redis://x", "war:1", "0") is False
    assert "presence read failed" in caplog.text
