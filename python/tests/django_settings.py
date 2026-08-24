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

# NOT applied globally: `session_auth_settings()` turns on CSRF_USE_SESSIONS, which
# hard-errors any request whose middleware stack lacks SessionMiddleware — and several
# modules here drive views through a trimmed stack. The session-auth tests opt in
# per-module instead (see tests/test_session_auth_module.py).

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        # A header credential first, then the session cookie — the shape BOTH consumers
        # run (one leads with an API-key authenticator, the other with personal tokens).
        # The order is load-bearing: DRF takes the unauthenticated status code from the
        # *first* authenticator's `authenticate_header()`, so this list answers 401.
        # `tests/test_session_auth_module.py` covers the session-only case separately,
        # which answers 403 — see that test for why it is not a bug in this package.
        "rest_framework.authentication.TokenAuthentication",
        "rest_framework.authentication.SessionAuthentication",
    ],
    "EXCEPTION_HANDLER": "drf_foundation.schemas.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "auth-login": "10/min",
        "auth-register": "10/hour",
        "auth-csrf": "60/min",
        "token-user": "120/min",
        # The MCP endpoint's per-key bucket. High enough not to interfere; the
        # throttling test patches the class attribute to something small.
        "mcp-key": "1000/min",
    },
}

TASK_TRIGGER_KEY = "test-task-key"
