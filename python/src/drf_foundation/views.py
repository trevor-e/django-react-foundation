"""Stock endpoints every project mounts as-is."""

from rest_framework.decorators import api_view, throttle_classes
from rest_framework.request import Request
from rest_framework.response import Response

from drf_foundation.permissions import public_endpoint
from drf_foundation.schemas import Schema, ok


class HealthCheck(Schema):
    status: str


@api_view(["GET"])
@public_endpoint
# Deliberately unthrottled: probed regularly by platform deploy healthchecks
# (blueprint §11b) and uptime monitors.
@throttle_classes([])
def health_check(request: Request) -> Response:
    """Mount at `api/health` — the platform healthcheck target. Pair with
    `settings_helpers.production_security_settings`, which exempts exactly that
    path from the SSL redirect."""
    return ok(HealthCheck(status="ok"))
