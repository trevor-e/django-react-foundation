from drf_foundation.permissions import public_endpoint
from drf_foundation.schemas import ok
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.request import Request
from rest_framework.response import Response

from config.schemas import HealthCheck


@api_view(["GET"])
@public_endpoint
# Deliberately unthrottled: probed regularly by the platform healthcheck (§11b) and
# uptime monitors.
@throttle_classes([])
def health_check(request: Request) -> Response:
    return ok(HealthCheck(status="ok"))
