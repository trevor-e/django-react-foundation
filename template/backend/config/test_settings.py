"""Test-time overrides (blueprint §5): throwaway Postgres on a non-default port,
fast password hashing, LocMem cache, eager Celery, console email, no throttling."""

from .settings import *  # noqa: F403
from .settings import REST_FRAMEWORK as _BASE_REST_FRAMEWORK

DEBUG = False

DATABASES["default"].update(  # noqa: F405
    {
        "HOST": "localhost",
        "PORT": "5433",
        "NAME": "__PROJECT___test",
        "USER": "__PROJECT___test",
        "PASSWORD": "__PROJECT___test",
    }
)

PASSWORD_HASHERS = ["django.contrib.auth.hashers.MD5PasswordHasher"]

CACHES = {
    "default": {"BACKEND": "django.core.cache.backends.locmem.LocMemCache"},
}

CELERY_TASK_ALWAYS_EAGER = True
CELERY_TASK_EAGER_PROPAGATES = True

REALTIME_PUBLISH = False

EMAIL_BACKEND = "django.core.mail.backends.locmem.EmailBackend"

REST_FRAMEWORK = {
    **_BASE_REST_FRAMEWORK,
    "DEFAULT_THROTTLE_CLASSES": [],
    # `None` (not just an absent key) disables a scope: views that set
    # throttle_classes directly bypass DEFAULT_THROTTLE_CLASSES, so an absent key
    # would raise ImproperlyConfigured rather than silently skip throttling.
    "DEFAULT_THROTTLE_RATES": {
        "anon": None,
        "user": None,
        "auth-login": None,
        "auth-register": None,
        "token-user": None,
    },
}
