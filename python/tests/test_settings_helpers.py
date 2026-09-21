import re

import pytest

from drf_foundation.settings_helpers import (
    allowed_hosts_from_env,
    check_pooled_connection,
    pooled_database,
    production_security_settings,
    redis_cache,
)


def test_pooled_database_from_url():
    config = pooled_database(env={"DATABASE_URL": "postgresql://u:p@db.internal:5432/app"})
    assert config["HOST"] == "db.internal"
    pool = dict(config["OPTIONS"]["pool"])
    assert pool.pop("check") is check_pooled_connection
    assert pool == {"min_size": 1, "max_size": 5, "timeout": 10}


def test_pooled_database_from_postgres_vars():
    config = pooled_database(default_name="myapp", env={"POSTGRES_PORT": "5433"})
    assert config["NAME"] == "myapp"
    assert config["PORT"] == "5433"
    assert config["OPTIONS"]["pool"]["max_size"] == 5


def test_pooled_database_checks_connections_on_checkout_by_default():
    """A connection the server closed while idle (restart, failover, idle timeout) must
    be replaced at checkout, not handed to a request to fail there."""
    config = pooled_database(env={})
    assert config["OPTIONS"]["pool"]["check"] is check_pooled_connection


def test_pooled_database_checkout_check_can_be_disabled():
    config = pooled_database(check_on_checkout=False, env={})
    assert "check" not in config["OPTIONS"]["pool"]


def test_check_pooled_connection_is_one_round_trip(monkeypatch):
    """Without psycopg_pool installed the check reduces to a bare round trip; with it,
    the pool's own check_connection runs (covered by the consumer against Postgres)."""
    import sys

    monkeypatch.setitem(sys.modules, "psycopg_pool", None)  # force the ImportError branch
    executed = []

    class Conn:
        def execute(self, sql):
            executed.append(sql)

    check_pooled_connection(Conn())
    assert executed == ["SELECT 1"]


def test_check_pooled_connection_raises_when_the_round_trip_fails(monkeypatch):
    import sys

    monkeypatch.setitem(sys.modules, "psycopg_pool", None)

    class DeadConn:
        def execute(self, sql):
            raise OSError("connection closed")

    with pytest.raises(OSError):
        check_pooled_connection(DeadConn())


def test_pooled_database_bounds_are_tunable():
    config = pooled_database(max_size=10, env={})
    assert config["OPTIONS"]["pool"]["max_size"] == 10


def test_pooled_database_sets_a_connect_timeout():
    # The dial itself must be bounded, not just the pool-slot wait: a black-holed
    # route (SYNs dropped, no RST) otherwise hangs each connect for the OS
    # default (~130s) — the 2026-07-19 outage mode.
    for config in (
        pooled_database(env={"DATABASE_URL": "postgresql://u:p@db.internal:5432/app"}),
        pooled_database(env={}),
    ):
        assert config["OPTIONS"]["connect_timeout"] == 5


def test_pooled_database_connect_timeout_is_tunable():
    config = pooled_database(connect_timeout=3, env={})
    assert config["OPTIONS"]["connect_timeout"] == 3


def test_redis_cache_from_url_with_socket_timeouts():
    config = redis_cache(env={"REDIS_URL": "redis://:secret@redis.internal:6379"})
    assert config["BACKEND"] == "django.core.cache.backends.redis.RedisCache"
    assert config["LOCATION"] == "redis://:secret@redis.internal:6379"
    # redis-py's default is socket_timeout=None — block forever; never ship that.
    assert config["OPTIONS"]["socket_connect_timeout"] == 2.0
    assert config["OPTIONS"]["socket_timeout"] == 2.0


def test_redis_cache_from_host_port_vars():
    config = redis_cache(env={"REDIS_HOST": "cache", "REDIS_PORT": "6380"})
    assert config["LOCATION"] == "redis://cache:6380"


def test_production_security_settings_exempts_the_health_path():
    settings = production_security_settings()
    assert settings["SECURE_SSL_REDIRECT"] is True
    (pattern,) = settings["SECURE_REDIRECT_EXEMPT"]
    # Matched against the path with the leading slash stripped.
    assert re.match(pattern, "api/health")
    assert not re.match(pattern, "api/anything-else")


def test_production_security_settings_satisfy_the_core_check():
    settings = production_security_settings()
    assert settings["SECURE_HSTS_SECONDS"] >= 31536000
    assert settings["SESSION_COOKIE_SECURE"] is True


def test_allowed_hosts_include_healthcheck_prober():
    hosts = allowed_hosts_from_env(env={"ALLOWED_HOSTS": "api.example.com"})
    assert hosts == ["api.example.com", "healthcheck.railway.app"]


def test_allowed_hosts_append_railway_domain():
    hosts = allowed_hosts_from_env(env={"RAILWAY_PUBLIC_DOMAIN": "x.up.railway.app"})
    assert "x.up.railway.app" in hosts


def test_pooled_database_bounds_from_env():
    config = pooled_database(
        env={"DB_POOL_MIN_SIZE": "2", "DB_POOL_MAX_SIZE": "8", "DB_POOL_TIMEOUT": "5"}
    )
    pool = dict(config["OPTIONS"]["pool"])
    assert pool.pop("check") is check_pooled_connection
    assert pool == {"min_size": 2, "max_size": 8, "timeout": 5}


def test_pooled_database_explicit_args_beat_env():
    config = pooled_database(max_size=3, env={"DB_POOL_MAX_SIZE": "8"})
    assert config["OPTIONS"]["pool"]["max_size"] == 3


def test_structlog_logging_returns_logging_dict():
    from drf_foundation.settings_helpers import structlog_logging

    config = structlog_logging(json_output=True)
    assert config["handlers"]["console"]["formatter"] == "structlog"
    assert config["loggers"]["django"]["propagate"] is False


def test_admin_csp_blocks_inline_scripts_but_allows_inline_styles():
    """The admin uses inline style attributes heavily; locking those down buys little
    while inline *scripts* stay blocked, which is the concession that matters."""
    from django.utils.csp import CSP

    from drf_foundation.settings_helpers import admin_csp

    policy = admin_csp()
    assert CSP.UNSAFE_INLINE not in policy["script-src"]
    assert CSP.UNSAFE_INLINE in policy["style-src"]


def test_admin_csp_lists_the_nonce_sentinel():
    """Stripped from the header unless a template actually reads csp_nonce, so it
    costs nothing until a Django-rendered inline script needs it."""
    from django.utils.csp import CSP

    from drf_foundation.settings_helpers import admin_csp

    assert CSP.NONCE in admin_csp()["script-src"]


def test_admin_csp_denies_framing_and_plugins():
    from django.utils.csp import CSP

    from drf_foundation.settings_helpers import admin_csp

    policy = admin_csp()
    assert policy["frame-ancestors"] == [CSP.NONE]
    assert policy["object-src"] == [CSP.NONE]
