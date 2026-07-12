"""Rate limiting for personal-API-token traffic and anonymous auth endpoints."""

from typing import TYPE_CHECKING

from rest_framework.authtoken.models import Token
from rest_framework.request import Request
from rest_framework.throttling import AnonRateThrottle, UserRateThrottle

if TYPE_CHECKING:
    # Runtime import would be circular: DRF resolves DEFAULT_THROTTLE_CLASSES while
    # rest_framework.views is still initializing (APIView's own class body).
    from rest_framework.views import APIView


class LoginRateThrottle(AnonRateThrottle):
    """Per-IP rate limit on the login endpoint.

    Login/register are anonymous by nature, so ``TokenUserRateThrottle`` (below)
    never applies to them — without this they'd have no brute-force protection at
    all. Attach explicitly via ``throttle_classes`` on the view rather than
    ``DEFAULT_THROTTLE_CLASSES`` so it can't accidentally throttle unrelated
    anonymous reads.
    """

    scope = "auth-login"


class RegisterRateThrottle(AnonRateThrottle):
    """Per-IP rate limit on the registration endpoint (signup abuse/spam)."""

    scope = "auth-register"


class TokenUserRateThrottle(UserRateThrottle):
    """Per-user rate limit applied only to token-authenticated requests.

    A leaked or runaway personal API token shouldn't be able to hammer the API,
    but everything else must stay unthrottled: JWT requests carry a SimpleJWT
    validated token as ``request.auth``, shared-key ops requests carry no
    recognized credential at all (``request.auth is None``), and anonymous public
    reads have no auth either — none of them are ``Token`` instances, so all
    bypass. Throttle state lives in the default Django cache (Redis in prod,
    shared across workers).
    """

    scope = "token-user"

    def allow_request(self, request: Request, view: "APIView") -> bool:
        if not isinstance(request.auth, Token):
            return True
        return super().allow_request(request, view)
