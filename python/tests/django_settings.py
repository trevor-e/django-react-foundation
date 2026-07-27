"""Minimal Django settings for exercising drf_foundation in isolation."""

SECRET_KEY = "test-secret-key-not-for-production"
DEBUG = True
BASE_DIR = "/tmp/drf-foundation-tests"

USE_TZ = True

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": ":memory:",
    }
}

INSTALLED_APPS = [
    "django.contrib.contenttypes",
    "django.contrib.auth",
    "django.contrib.sessions",
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_foundation",
    "tests.testapp",
]

# Only the session/CSRF/auth chain — the package's session_auth module needs it, and
# both middlewares must be present for CSRF enforcement to be exercised for real.
MIDDLEWARE = [
    "django.contrib.sessions.middleware.SessionMiddleware",
    "django.middleware.csrf.CsrfViewMiddleware",
    "django.contrib.auth.middleware.AuthenticationMiddleware",
]

ROOT_URLCONF = "tests.urls"

# Rotating refresh + blacklist — what drf_foundation.auth's contract assumes.
from drf_foundation.settings_helpers import (  # noqa: E402
    session_auth_settings,
    simple_jwt_defaults,
)

SIMPLE_JWT = simple_jwt_defaults()

# Both auth modules are exercised side by side here; a real project picks one.
globals().update(session_auth_settings(cross_origin_spa=True))

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "EXCEPTION_HANDLER": "drf_foundation.schemas.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "auth-login": "10/min",
        "auth-register": "10/hour",
        "auth-csrf": "60/min",
        "token-user": "120/min",
    },
}

TASK_TRIGGER_KEY = "test-task-key"
