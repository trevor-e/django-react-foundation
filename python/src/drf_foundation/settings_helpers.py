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
    min_size: int | None = None,
    max_size: int | None = None,
    timeout: int | None = None,
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

    Pool bounds resolve from the env when not passed explicitly — ``DB_POOL_MIN_SIZE``
    (default 1), ``DB_POOL_MAX_SIZE`` (default 5), ``DB_POOL_TIMEOUT`` (default 10s)
    — so each deploy role (web / worker / beat) can be sized independently via
    platform vars, no code change.
    """
    e = os.environ if env is None else env
    pool = {
        "min_size": min_size if min_size is not None else int(e.get("DB_POOL_MIN_SIZE", "1")),
        "max_size": max_size if max_size is not None else int(e.get("DB_POOL_MAX_SIZE", "5")),
        "timeout": timeout if timeout is not None else int(e.get("DB_POOL_TIMEOUT", "10")),
    }
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


def admin_csp() -> dict[str, Any]:
    """``SECURE_CSP`` for the HTML a Django API origin actually serves.

    Worth naming the surface precisely, because it's smaller than it first looks: an
    API origin's docs route is usually a redirect and its schema route is JSON, so the
    only HTML Django renders here is **the admin plus Django's error pages**. This
    policy is tuned for that, not copied from a frontend's — and it complements rather
    than replaces a frontend CSP, which only covers the frontend's own origin.

    Deliberately **not** gated on production: unlike HSTS or SSL redirect, a CSP is
    meaningful in dev, and gating it means the first violation anyone sees is in prod.

    ``style-src`` allows inline attributes because the admin uses them heavily and
    locking them down buys little while inline *scripts* stay blocked. ``CSP.NONCE`` is
    a sentinel the middleware substitutes **only** if a template actually read
    ``csp_nonce``, and is stripped otherwise — so admin responses come back as a plain
    ``script-src 'self'`` while a future inline script in a Django-rendered template
    works without a settings change. Register
    ``django.template.context_processors.csp`` or the nonce entry is dead config.

    Requires ``django.middleware.csp.ContentSecurityPolicyMiddleware`` in MIDDLEWARE.
    """
    from django.utils.csp import CSP

    return {
        "default-src": [CSP.SELF],
        "script-src": [CSP.SELF, CSP.NONCE],
        "style-src": [CSP.SELF, CSP.UNSAFE_INLINE],
        "img-src": [CSP.SELF, "data:"],
        "font-src": [CSP.SELF],
        "connect-src": [CSP.SELF],
        "frame-ancestors": [CSP.NONE],
        "object-src": [CSP.NONE],
        "base-uri": [CSP.SELF],
        "form-action": [CSP.SELF],
    }


def session_auth_settings(
    *, cookie_age_days: int = 14, cross_origin_spa: bool = False
) -> dict[str, Any]:
    """Session-cookie browser auth (``drf_foundation.session_auth``), safe defaults.

    Apply unconditionally — every value here is correct in dev too, except
    ``SESSION_COOKIE_SECURE``, which ``production_security_settings()`` turns on in prod
    (dev has no HTTPS). Order the two so the production block wins.

    - ``CSRF_USE_SESSIONS`` keeps the CSRF secret in the session, so the product sets one
      cookie total and none readable by JavaScript. The token reaches the SPA as a
      response *value* instead (see :mod:`drf_foundation.session_auth`).
    - ``SameSite=Lax`` is the whole cross-site story: the cookie rides same-site XHR
      (including to an ``api.`` subdomain of the frontend's registrable domain) and is
      never sent cross-site. A frontend on a *different* site therefore cannot
      authenticate at all — that is the trade this module accepts, not a bug to patch
      with ``SameSite=None``.
    - Sliding expiry (``SESSION_SAVE_EVERY_REQUEST``): an active user is not logged out
      mid-use, an idle one expires ``cookie_age_days`` after their last request.

    ``cross_origin_spa=True`` adds the CORS credential flag a separate frontend origin
    needs. The project still owns ``CORS_ALLOWED_ORIGINS``/``CSRF_TRUSTED_ORIGINS`` —
    those are per-environment values, not doctrine.
    """
    settings: dict[str, Any] = {
        "SESSION_COOKIE_AGE": cookie_age_days * 24 * 60 * 60,
        "SESSION_COOKIE_HTTPONLY": True,
        "SESSION_COOKIE_SAMESITE": "Lax",
        "SESSION_EXPIRE_AT_BROWSER_CLOSE": False,
        "SESSION_SAVE_EVERY_REQUEST": True,
        "CSRF_USE_SESSIONS": True,
    }
    if cross_origin_spa:
        settings["CORS_ALLOW_CREDENTIALS"] = True
    return settings


def simple_jwt_defaults() -> dict[str, Any]:
    """SIMPLE_JWT matching the apiClient contract: rotating refresh tokens with
    blacklist (requires `rest_framework_simplejwt.token_blacklist` installed).

    JWT is the cross-site/native option; for a same-site first-party SPA prefer
    :func:`session_auth_settings` + :mod:`drf_foundation.session_auth`, where the
    credential is an ``HttpOnly`` cookie JavaScript cannot read."""
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


def structlog_logging(*, json_output: bool, level: str = "INFO") -> dict[str, Any]:
    """Configure structlog and return the Django ``LOGGING`` dict — one call in
    settings.py gives the whole process structured, context-carrying logs.

    Requires the ``logging`` extra (django-structlog; pairs with
    ``django_structlog.middlewares.RequestMiddleware`` in ``MIDDLEWARE`` and, for
    Celery, ``DJANGO_STRUCTLOG_CELERY_ENABLED = True`` + the
    ``DjangoStructLogInitStep`` worker step + ``CELERY_WORKER_HIJACK_ROOT_LOGGER =
    False``).

    Every line carries the contextvars django-structlog binds per request
    (``request_id``, ``user_id``, anything the app adds). ``json_output=True`` renders
    JSON for platform log search (production); ``False`` renders pretty console lines
    for humans (dev). stdlib loggers (``django.*``, ``celery.*``) are routed through
    the same formatter via ``foreign_pre_chain``, so the two worlds don't diverge.

    ```python
    LOGGING = structlog_logging(json_output=is_production())
    ```
    """
    import structlog

    renderer: Any = (
        structlog.processors.JSONRenderer() if json_output else structlog.dev.ConsoleRenderer()
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.stdlib.filter_by_level,
            structlog.processors.TimeStamper(fmt="iso"),
            structlog.stdlib.add_logger_name,
            structlog.stdlib.add_log_level,
            structlog.stdlib.PositionalArgumentsFormatter(),
            structlog.processors.StackInfoRenderer(),
            structlog.processors.format_exc_info,
            structlog.processors.UnicodeDecoder(),
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )
    return {
        "version": 1,
        "disable_existing_loggers": False,
        "formatters": {
            "structlog": {
                "()": structlog.stdlib.ProcessorFormatter,
                "processor": renderer,
                "foreign_pre_chain": [
                    structlog.contextvars.merge_contextvars,
                    structlog.processors.TimeStamper(fmt="iso"),
                    structlog.stdlib.add_logger_name,
                    structlog.stdlib.add_log_level,
                    structlog.stdlib.ExtraAdder(),
                ],
            },
        },
        "handlers": {
            "console": {"class": "logging.StreamHandler", "formatter": "structlog"},
        },
        "root": {"handlers": ["console"], "level": level},
        "loggers": {
            # Explicit handler + no propagation, or Django's default config double-logs
            # django.* through both its own console handler and root.
            "django": {"handlers": ["console"], "level": level, "propagate": False},
        },
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
