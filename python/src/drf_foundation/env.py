"""Production-environment detection (blueprint §11b's companion).

Settings and fail-closed checks both need one answer to "is this production?" that a
misconfigured env var can't turn off. Platform markers (Railway injects these into
every service) are a non-overridable production signal; an explicit app env var is
the portable one.
"""

import os

_RAILWAY_MARKERS = ("RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "RAILWAY_PUBLIC_DOMAIN")


def is_production(app_env_var: str = "APP_ENV") -> bool:
    """True when `app_env_var` says "production" or any platform marker is present.

    Projects with their own env-var name wrap it once:

    ```python
    # config/env.py
    from drf_foundation.env import is_production as _is_production

    def is_production() -> bool:
        return _is_production("MYAPP_ENV")
    ```
    """
    if os.environ.get(app_env_var, "").lower() == "production":
        return True
    return any(os.environ.get(marker) for marker in _RAILWAY_MARKERS)
