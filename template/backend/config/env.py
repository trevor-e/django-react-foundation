"""Production-environment detection, shared by `settings.py` and `config/checks.py`.

Lives outside `checks.py` so `settings.py` can import it during Django's own settings
load (importing from `checks.py` there would risk a premature app-registry import,
since `checks.py` sits in an installed app).
"""

import os

# Railway injects these into every service; their presence is a non-overridable
# production marker — no env var may declare a Railway environment non-production.
_RAILWAY_MARKERS = ("RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "RAILWAY_PUBLIC_DOMAIN")


def is_production() -> bool:
    if os.environ.get("APP_ENV", "").lower() == "production":
        return True
    return any(os.environ.get(marker) for marker in _RAILWAY_MARKERS)
