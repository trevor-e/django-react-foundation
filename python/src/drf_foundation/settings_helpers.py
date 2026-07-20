"""Settings fragments shared across projects (blueprint §1b, §11b).

Settings stay a project-owned, readable file — these helpers return plain dicts for
the blocks that are pure doctrine (connection pooling, production security headers),
so the doctrine flows with pin bumps while everything app-specific stays literal in
settings.py:

```python
# settings.py
from drf_foundation.settings_helpers import pooled_database, production_security_settings

DATABASES = {"default": pooled_database(default_name="myapp")}
if is_production():
    globals().update(production_security_settings())
```
"""

import os
from typing import Any

import dj_database_url


def pooled_database(
    *,
    default_name: str = "app",
    min_size: int = 1,
    max_size: int = 5,
    timeout: int = 10,
    connect_timeout: int = 5,
    env: os._Environ | dict[str, str] | None = None,
) -> dict[str, Any]:
    """The `DATABASES["default"]` entry: DATABASE_URL (platform convention) when set,
    POSTGRES_* otherwise, always with psycopg3's built-in pool.

    Pooled, never CONN_MAX_AGE (§1b): under ASGI each request's sync code runs on its
    own short-lived thread, so thread-affine persistent connections strand and leak.
    Bounds are explicit because bare ``pool: True`` is an eager fixed-4 *per process*
    — web, beat, and every Celery prefork child each get one.

    Two distinct timeouts, both required (§1c — the 2026-07-19 pystonks outage):
    ``timeout`` bounds how long a request waits for a pool *slot*; ``connect_timeout``
    bounds the TCP+auth *dial* itself. Without the latter, a black-holed route (SYNs
    dropped, no RST — the platform-mesh failure mode) hangs each connection attempt
    for the OS default (~130s), and requests, migrations, and Celery tasks all
    inherit it. With both, an unreachable database turns into fast, loggable errors
    instead of silently starving the worker pool.
    """
    e = os.environ if env is None else env
    pool = {"min_size": min_size, "max_size": max_size, "timeout": timeout}
    if e.get("DATABASE_URL"):
        config: dict[str, Any] = dict(dj_database_url.parse(e["DATABASE_URL"]))
        config["OPTIONS"] = {
            **config.get("OPTIONS", {}),
            "pool": pool,
            "connect_timeout": connect_timeout,
        }
        return config
    return {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": e.get("POSTGRES_DB", default_name),
        "USER": e.get("POSTGRES_USER", default_name),
        "PASSWORD": e.get("POSTGRES_PASSWORD", default_name),
        "HOST": e.get("POSTGRES_HOST", "localhost"),
        "PORT": e.get("POSTGRES_PORT", "5432"),
        "OPTIONS": {"pool": pool, "connect_timeout": connect_timeout},
    }


def redis_cache(
    *,
    connect_timeout: float = 2.0,
    socket_timeout: float = 2.0,
    env: os._Environ | dict[str, str] | None = None,
) -> dict[str, Any]:
    """The `CACHES["default"]` entry: REDIS_URL when set (platform convention —
    required when the broker needs auth credentials), REDIS_HOST/REDIS_PORT
    otherwise, always with socket timeouts.

    Django's built-in RedisCache passes OPTIONS through to redis-py's connection
    pool, and redis-py's default is ``socket_timeout=None`` — block forever. Any
    cache-touching request path (DRF throttles, session cache, page cache) then
    inherits an unbounded hang when the route to Redis black-holes (§1c). Two
    seconds is generous for an in-network cache; a cache that can't answer in two
    seconds should be treated as down.
    """
    e = os.environ if env is None else env
    url = e.get("REDIS_URL") or (
        f"redis://{e.get('REDIS_HOST', 'localhost')}:{e.get('REDIS_PORT', '6379')}"
    )
    return {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": url,
        "OPTIONS": {
            "socket_connect_timeout": connect_timeout,
            "socket_timeout": socket_timeout,
        },
    }


def production_security_settings(
    *, health_path: str = "api/health", hsts_seconds: int = 31536000
) -> dict[str, Any]:
    """The production security-header block, healthcheck-safe (§11b).

    Apply only under the production gate (``globals().update(...)`` inside
    ``if is_production():``) and keep a fail-closed check asserting it's active
    (`drf_foundation.checks.core_production_messages` covers that).

    - TLS terminates at the platform edge → trust one X-Forwarded-Proto hop, else
      SECURE_SSL_REDIRECT loops.
    - The deploy healthcheck probes plain HTTP with no X-Forwarded-Proto → exempt
      exactly the health path or every probe 301s and no deploy goes healthy.
    - HSTS a year, subdomains, no preload (preload is a one-way door).
    """
    return {
        "SECURE_PROXY_SSL_HEADER": ("HTTP_X_FORWARDED_PROTO", "https"),
        "SECURE_SSL_REDIRECT": True,
        "SECURE_REDIRECT_EXEMPT": [rf"^{health_path}$"],
        "SECURE_HSTS_SECONDS": hsts_seconds,
        "SECURE_HSTS_INCLUDE_SUBDOMAINS": True,
        "SECURE_HSTS_PRELOAD": False,
        "SECURE_CONTENT_TYPE_NOSNIFF": True,
        "SECURE_REFERRER_POLICY": "same-origin",
        "SESSION_COOKIE_SECURE": True,
    }


def simple_jwt_defaults() -> dict[str, Any]:
    """SIMPLE_JWT matching the apiClient contract: rotating refresh tokens with
    blacklist (requires `rest_framework_simplejwt.token_blacklist` installed)."""
    from datetime import timedelta

    return {
        "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
        "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
        "ROTATE_REFRESH_TOKENS": True,
        "BLACKLIST_AFTER_ROTATION": True,
        "AUTH_HEADER_TYPES": ("Bearer",),
        "USER_ID_FIELD": "id",
        "USER_ID_CLAIM": "user_id",
    }


def allowed_hosts_from_env(*, env: os._Environ | dict[str, str] | None = None) -> list[str]:
    """ALLOWED_HOSTS from the env plus the two hosts deploys need: the platform's
    public domain and the healthcheck prober's Host header (§11b — without it every
    probe 400s, invisibly: DisallowedHost logs to the null handler)."""
    e = os.environ if env is None else env
    hosts = [h for h in e.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]
    if e.get("RAILWAY_PUBLIC_DOMAIN"):
        hosts.append(e["RAILWAY_PUBLIC_DOMAIN"])
    hosts.append("healthcheck.railway.app")
    return hosts
