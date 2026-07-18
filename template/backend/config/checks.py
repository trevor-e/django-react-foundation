"""Fail-closed production configuration checks (blueprint's ADR-0014 pattern).

System checks run on every `manage.py` command (so Railway's pre-deploy `migrate`
fails a bad deploy) and `entrypoint.sh` runs `manage.py check --fail-level ERROR`
before exec'ing each role (granian/celery don't run system checks). Outside
production (`is_production()`), everything here is a no-op.

Extend this per provider seam you add: any env-var-selected stub that would silently
fail open in production deserves an Error here (see the reference app's checks.py for
the acknowledgment-list pattern via a PROD_ALLOWED_STUBS variable).
"""

from django.conf import settings
from django.core import checks

from config.env import is_production

# The settings.py fallback — booting prod with it means SECRET_KEY was never set.
INSECURE_SECRET_KEY_FALLBACK = "django-insecure-local-dev-only-do-not-use-in-prod"


@checks.register(checks.Tags.security)
def check_production_config(
    app_configs: object = None, **kwargs: object
) -> list[checks.CheckMessage]:
    if not is_production():
        return []

    errors: list[checks.CheckMessage] = []

    if settings.DEBUG:
        errors.append(
            checks.Error(
                "DEBUG is enabled in production.",
                hint="Set DEBUG=false on this service.",
                id="app.E001",
            )
        )

    secret = settings.SECRET_KEY or ""
    if secret == INSECURE_SECRET_KEY_FALLBACK or len(secret) < 50:
        errors.append(
            checks.Error(
                "SECRET_KEY is unset, the insecure dev fallback, or too short.",
                hint="Set SECRET_KEY to a random value of at least 50 characters.",
                id="app.E002",
            )
        )

    # Not per-header: this checks the settings.py is_production() gate's effect, so a
    # regression of that whole block (deleted, mis-indented) is caught.
    if settings.SECURE_HSTS_SECONDS < 31536000 or not settings.SESSION_COOKIE_SECURE:
        errors.append(
            checks.Error(
                "Production security headers are disabled or weakened.",
                hint="Check the `if is_production():` block in settings.py.",
                id="app.E003",
            )
        )

    return errors
