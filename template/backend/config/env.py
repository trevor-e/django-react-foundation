"""Project wrapper over the package's production detection — one place to name the
project's own env marker; settings.py and checks.py both import from here."""

from drf_foundation.env import is_production as _is_production


def is_production() -> bool:
    return _is_production("APP_ENV")
