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

# APP_DIRS so the email templates shipped inside drf_foundation/templates/ resolve.
TEMPLATES = [
    {
        "BACKEND": "django.template.backends.django.DjangoTemplates",
        "DIRS": [],
        "APP_DIRS": True,
        "OPTIONS": {"context_processors": []},
    }
]

DEFAULT_FROM_EMAIL = "noreply@example.com"
EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"
FRONTEND_BASE_URL = "https://app.example.com"

# Rotating refresh + blacklist — what drf_foundation.auth's contract assumes.
from drf_foundation.settings_helpers import simple_jwt_defaults  # noqa: E402

SIMPLE_JWT = simple_jwt_defaults()

# NOT applied globally: `session_auth_settings()` turns on CSRF_USE_SESSIONS, which
# hard-errors any request whose middleware stack lacks SessionMiddleware — and several
# modules here drive views through a trimmed stack. The session-auth tests opt in
# per-module instead (see tests/test_session_auth_module.py).

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
