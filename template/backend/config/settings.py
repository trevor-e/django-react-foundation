"""
Django settings for __PROJECT__.

12-factor: everything env-driven with sane local defaults. See config/test_settings.py
for the test-time overrides (blueprint §5). Serving is ASGI-first (blueprint §11a) —
granian in dev and prod, sync DRF views on asgiref's per-request threads, pooled DB
connections (§1b), and healthcheck-safe production security headers (§11b).
"""

import os
from datetime import timedelta
from pathlib import Path

import dj_database_url
from corsheaders.defaults import default_headers

from config.env import is_production

BASE_DIR = Path(__file__).resolve().parent.parent

# Both fallbacks are dev-only: the fail-closed prod checks (config/checks.py) refuse
# to boot production with this SECRET_KEY or with DEBUG on.
SECRET_KEY = os.environ.get("SECRET_KEY", "django-insecure-local-dev-only-do-not-use-in-prod")

DEBUG = os.environ.get("DEBUG", "true").lower() == "true"

ALLOWED_HOSTS = [h for h in os.environ.get("ALLOWED_HOSTS", "localhost,127.0.0.1").split(",") if h]
if os.environ.get("RAILWAY_PUBLIC_DOMAIN"):
    ALLOWED_HOSTS.append(os.environ["RAILWAY_PUBLIC_DOMAIN"])
# Railway's deploy healthcheck probes with this Host header (blueprint §11b); without
# it every probe 400s — and invisibly (django.security.DisallowedHost logs to the
# null handler by default).
ALLOWED_HOSTS.append("healthcheck.railway.app")


INSTALLED_APPS = [
    "django.contrib.admin",
    "django.contrib.auth",
    "django.contrib.contenttypes",
    "django.contrib.sessions",
    "django.contrib.messages",
    "django.contrib.staticfiles",
    "rest_framework",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "corsheaders",
    "drf_foundation",
    "accounts",
    # Registers the fail-closed production config checks (config/checks.py).
    "config",
]

MIDDLEWARE = [
    # First so every later consumer of the body sees a CONTENT_LENGTH: proxies
    # (Cloudflare) re-frame POST bodies as chunked and DRF treats a missing
    # Content-Length as an empty body (blueprint §11a).
    "drf_foundation.middleware.ChunkedContentLengthMiddleware",
    "django.middleware.security.SecurityMiddleware",
    "whitenoise.middleware.WhiteNoiseMiddleware",
    "corsheaders.middleware.CorsMiddleware",
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.common.CommonMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
    "django.contrib.messages.middleware.MessageMiddleware",
    "django.middleware.clickjacking.XFrameOptionsMiddleware",
]

ROOT_URLCONF = "config.urls"

TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {
            "context_processors": [
                "django.template.context_processors.request",
                "django.contrib.auth.context_processors.auth",
                "django.contrib.messages.context_processors.messages",
            ],
        },
    },
]

# Kept for tooling that imports it; nothing serves WSGI (blueprint §11a).
WSGI_APPLICATION = "config.wsgi.application"


# Database — Postgres only. DATABASE_URL (Railway's convention) wins when set;
# otherwise POSTGRES_* for local dev. psycopg3's built-in pool, never CONN_MAX_AGE
# (blueprint §1b): under ASGI each request's sync code runs on its own short-lived
# thread, so thread-affine persistent connections strand and leak. Explicit bounds
# because bare `pool: True` is an eager fixed-4 per process.
_DB_POOL = {"min_size": 1, "max_size": 5, "timeout": 10}
if os.environ.get("DATABASE_URL"):
    _default_db: dict = dict(dj_database_url.parse(os.environ["DATABASE_URL"]))
    _default_db["OPTIONS"] = {**_default_db.get("OPTIONS", {}), "pool": _DB_POOL}
    DATABASES = {"default": _default_db}
else:
    DATABASES = {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.environ.get("POSTGRES_DB", "__PROJECT__"),
            "USER": os.environ.get("POSTGRES_USER", "__PROJECT__"),
            "PASSWORD": os.environ.get("POSTGRES_PASSWORD", "__PROJECT__"),
            "HOST": os.environ.get("POSTGRES_HOST", "localhost"),
            "PORT": os.environ.get("POSTGRES_PORT", "5432"),
            "OPTIONS": {"pool": _DB_POOL},
        }
    }

AUTH_USER_MODEL = "accounts.User"

AUTH_PASSWORD_VALIDATORS = [
    {"NAME": "django.contrib.auth.password_validation.UserAttributeSimilarityValidator"},
    {"NAME": "django.contrib.auth.password_validation.MinimumLengthValidator"},
    {"NAME": "django.contrib.auth.password_validation.CommonPasswordValidator"},
    {"NAME": "django.contrib.auth.password_validation.NumericPasswordValidator"},
]


LANGUAGE_CODE = "en-us"
TIME_ZONE = "UTC"
USE_I18N = True
USE_TZ = True

STATIC_URL = "static/"
STATIC_ROOT = BASE_DIR / "staticfiles"
STORAGES = {
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "whitenoise.storage.CompressedManifestStaticFilesStorage"},
}

DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"

# Shared cache for DRF throttle counters: must be shared across every server
# worker/replica, or each process throttles independently and the effective rate
# multiplies by worker count. test_settings.py overrides to LocMem.
CACHES = {
    "default": {
        "BACKEND": "django.core.cache.backends.redis.RedisCache",
        "LOCATION": os.environ.get("REDIS_URL", "redis://localhost:6379/0"),
    },
}

# DRF + drf_foundation: deny-by-default permissions, envelope exception handler
# (blueprint §3a, §4), a generous global backstop throttle plus tight rates on the
# auth endpoints (the package's throttles carry the auth-* scopes).
REST_FRAMEWORK: dict[str, object] = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "EXCEPTION_HANDLER": "drf_foundation.schemas.api_exception_handler",
    "DEFAULT_THROTTLE_CLASSES": [
        "rest_framework.throttling.AnonRateThrottle",
        "rest_framework.throttling.UserRateThrottle",
    ],
    "DEFAULT_THROTTLE_RATES": {
        "anon": "60/min",
        "user": "300/min",
        "auth-login": "10/min",
        "auth-register": "10/hour",
        "token-user": "120/min",
    },
}
# Behind Railway's edge proxy, DRF's client-IP helper must skip exactly one trusted
# proxy hop to read the real client IP from X-Forwarded-For.
if is_production():
    REST_FRAMEWORK["NUM_PROXIES"] = 1

# Wire schema export (drf_foundation, blueprint §3b).
WIRE_SCHEMA_OUTPUT = BASE_DIR.parent / "frontend" / "src" / "types" / "api-schema.json"
WIRE_SCHEMA_TITLE = "__PROJECT__ API"

SIMPLE_JWT = {
    "ACCESS_TOKEN_LIFETIME": timedelta(minutes=30),
    "REFRESH_TOKEN_LIFETIME": timedelta(days=14),
    "ROTATE_REFRESH_TOKENS": True,
    "BLACKLIST_AFTER_ROTATION": True,
    "AUTH_HEADER_TYPES": ("Bearer",),
    "USER_ID_FIELD": "id",
    "USER_ID_CLAIM": "user_id",
}

# CORS: local dev frontend + prod frontend origin(s), comma-separated (blueprint §14).
CORS_ALLOWED_ORIGINS = [
    o for o in os.environ.get("CORS_ALLOWED_ORIGINS", "http://localhost:5173").split(",") if o
]
CORS_ALLOW_HEADERS = (*default_headers, "sentry-trace", "baggage")

# The frontend origin, for building links (e.g. emails) into it.
FRONTEND_BASE_URL = os.environ.get("FRONTEND_BASE_URL", "http://localhost:5173")

# Celery (blueprint §Async): broker/backend from env; eager mode in test_settings.
CELERY_BROKER_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_RESULT_BACKEND = os.environ.get("REDIS_URL", "redis://localhost:6379/0")
CELERY_TIMEZONE = "UTC"
CELERY_TASK_TRACK_STARTED = True

# Realtime change notifications (drf_foundation.realtime + createRealtimeSync):
# publish change ids to Redis pub/sub at your domain-event choke point and stream
# them over SSE. Off in test_settings.
REALTIME_PUBLISH = True

# Console email by default (prints to stdout); swap per-provider when one lands.
EMAIL_BACKEND = os.environ.get("EMAIL_BACKEND", "django.core.mail.backends.console.EmailBackend")
DEFAULT_FROM_EMAIL = os.environ.get("DEFAULT_FROM_EMAIL", "hello@example.com")

# Sentry — set SENTRY_DSN to enable; no-op otherwise.
SENTRY_DSN = os.environ.get("SENTRY_DSN", "")
if SENTRY_DSN:
    import sentry_sdk

    sentry_sdk.init(
        dsn=SENTRY_DSN,
        environment=os.environ.get("APP_ENV", "development"),
        traces_sample_rate=float(os.environ.get("SENTRY_TRACES_SAMPLE_RATE", "1.0")),
        send_default_pii=False,
    )

# Production security headers, gated on the same is_production() as the fail-closed
# checks (config/checks.py refuses to boot production if this block is weakened).
if is_production():
    # Railway terminates TLS at its edge and forwards over HTTP with one proxy hop;
    # without this, SECURE_SSL_REDIRECT would redirect-loop.
    SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
    SECURE_SSL_REDIRECT = True
    # Railway's healthcheck probes over plain HTTP with no X-Forwarded-Proto —
    # without this exemption the redirect 301s every probe and no deploy ever goes
    # healthy (blueprint §11b).
    SECURE_REDIRECT_EXEMPT = [r"^api/health$"]
    SECURE_HSTS_SECONDS = 31536000
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = False
    SECURE_CONTENT_TYPE_NOSNIFF = True
    SECURE_REFERRER_POLICY = "same-origin"
    SESSION_COOKIE_SECURE = True
