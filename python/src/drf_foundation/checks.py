"""Core fail-closed production checks (blueprint's fail-closed pattern).

Pure message builders — the *project* registers them (and adds its own provider-seam
checks alongside), keeping check IDs and registration timing under project control:

```python
# config/checks.py
from django.core import checks
from drf_foundation.checks import core_production_messages
from config.env import is_production

@checks.register(checks.Tags.security)
def check_production_config(app_configs=None, **kwargs):
    if not is_production():
        return []
    errors = core_production_messages(id_prefix="myapp")
    # ... append project-specific seam checks here ...
    return errors
```
"""

from django.conf import settings
from django.core import checks

# The conventional settings.py fallback — booting prod with it means SECRET_KEY was
# never set. Override via `insecure_fallback` if a project uses a different sentinel.
INSECURE_SECRET_KEY_FALLBACK = "django-insecure-local-dev-only-do-not-use-in-prod"


def core_production_messages(
    *,
    id_prefix: str = "app",
    headers_check_id: str | None = None,
    insecure_fallback: str = INSECURE_SECRET_KEY_FALLBACK,
) -> list[checks.CheckMessage]:
    """The three checks every production Django deploy needs, as Error messages.

    - `{id_prefix}.E001`: DEBUG enabled.
    - `{id_prefix}.E002`: SECRET_KEY unset/fallback/short.
    - `headers_check_id` (default `{id_prefix}.E003`): the production security-header
      block (HSTS ≥ 1y + secure session cookie) is absent or weakened — a regression
      of the whole `if is_production():` settings block, not a per-header audit.

    Callers are responsible for the production gate (these assume it already passed).
    """
    errors: list[checks.CheckMessage] = []

    if settings.DEBUG:
        errors.append(
            checks.Error(
                "DEBUG is enabled in production.",
                hint="Set DEBUG=false on this service.",
                id=f"{id_prefix}.E001",
            )
        )

    secret = settings.SECRET_KEY or ""
    if secret == insecure_fallback or len(secret) < 50:
        errors.append(
            checks.Error(
                "SECRET_KEY is unset, the insecure dev fallback, or too short for production.",
                hint="Set SECRET_KEY to a random value of at least 50 characters.",
                id=f"{id_prefix}.E002",
            )
        )

    if getattr(settings, "SECURE_HSTS_SECONDS", 0) < 31536000 or not getattr(
        settings, "SESSION_COOKIE_SECURE", False
    ):
        errors.append(
            checks.Error(
                "Production security headers are disabled or weakened (HSTS under a"
                " year, or the session cookie isn't marked secure).",
                hint="Check the `if is_production():` security-header block in"
                " settings.py hasn't been removed or bypassed.",
                id=headers_check_id or f"{id_prefix}.E003",
            )
        )

    return errors
