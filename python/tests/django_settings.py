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
    "drf_foundation",
    "tests.testapp",
]

REST_FRAMEWORK = {
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
    "EXCEPTION_HANDLER": "drf_foundation.schemas.api_exception_handler",
    "DEFAULT_THROTTLE_RATES": {
        "auth-login": "10/min",
        "auth-register": "10/hour",
        "token-user": "120/min",
    },
}

TASK_TRIGGER_KEY = "test-task-key"
