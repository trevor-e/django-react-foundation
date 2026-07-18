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
    "rest_framework",
    "rest_framework.authtoken",
    "rest_framework_simplejwt",
    "rest_framework_simplejwt.token_blacklist",
    "drf_foundation",
    "tests.testapp",
]

ROOT_URLCONF = "tests.urls"

# Rotating refresh + blacklist — what drf_foundation.auth's contract assumes.
from drf_foundation.settings_helpers import simple_jwt_defaults  # noqa: E402

SIMPLE_JWT = simple_jwt_defaults()

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "DEFAULT_AUTHENTICATION_CLASSES": [
        "rest_framework_simplejwt.authentication.JWTAuthentication",
    ],
    "EXCEPTION_HANDLER": "drf_foundation.schemas.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "auth-login": "10/min",
        "auth-register": "10/hour",
        "token-user": "120/min",
    },
}

TASK_TRIGGER_KEY = "test-task-key"
