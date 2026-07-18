"""Fail-closed production configuration checks (blueprint's fail-closed pattern).

The core checks (DEBUG/SECRET_KEY/security headers) come from the package; this
module registers them and is where project-specific provider-seam checks accumulate
— any env-var-selected stub that would silently fail open in production deserves an
Error here (see the reference app's checks.py for the PROD_ALLOWED_STUBS
acknowledgment-list pattern)."""

from django.core import checks
from drf_foundation.checks import core_production_messages

from config.env import is_production


@checks.register(checks.Tags.security)
def check_production_config(
    app_configs: object = None, **kwargs: object
) -> list[checks.CheckMessage]:
    if not is_production():
        return []
    errors = core_production_messages(id_prefix="app")
    # ... append project-specific seam checks here ...
    return errors
