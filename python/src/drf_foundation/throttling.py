"""Rate limiting for personal-API-token traffic and sensitive auth endpoints."""

from typing import TYPE_CHECKING

from rest_framework.authtoken.models import Token
from rest_framework.request import Request
from rest_framework.throttling import SimpleRateThrottle, UserRateThrottle

if TYPE_CHECKING:
    # Runtime import would be circular: DRF resolves DEFAULT_THROTTLE_CLASSES while
    # rest_framework.views is still initializing (APIView's own class body).
    from rest_framework.views import APIView


class IpKeyedThrottle(SimpleRateThrottle):
    """Base for throttles that key on the client IP **regardless of auth state**.

    Why not ``AnonRateThrottle``: its ``get_cache_key`` returns ``None`` for an
    authenticated request, which makes ``allow_request`` return ``True`` — i.e. the
    throttle silently stops applying the moment the caller has a session. For a login or
    signup endpoint that is backwards, and for registration it is worse than backwards:
    a successful signup *signs the new user in*, so under ``AnonRateThrottle`` the
    second and every later signup from that browser is unthrottled entirely.

    Nor ``ScopedRateThrottle``: it reads the scope from the view's ``throttle_scope``
    attribute, which DRF's ``@api_view`` does not copy onto function-based views. So
    subclasses hardcode ``scope`` as a class attribute and always key on IP, which works
    for function- and class-based views alike.

    Rates still come from ``DEFAULT_THROTTLE_RATES[scope]``, exactly as before.
    """

    def get_cache_key(self, request: Request, view: "APIView") -> str:
        return self.cache_format % {"scope": self.scope, "ident": self.get_ident(request)}


class LoginRateThrottle(IpKeyedThrottle):
    """Per-IP rate limit on the login endpoint, that a signed-in caller cannot bypass.

    ``TokenUserRateThrottle`` (below) never applies here — a login attempt carries no
    token — so without this there is no brute-force protection at all. Attach explicitly
    via ``throttle_classes`` on the view rather than ``DEFAULT_THROTTLE_CLASSES`` so it
    can't accidentally throttle unrelated reads.
    """

    scope = "auth-login"


class RegisterRateThrottle(IpKeyedThrottle):
    """Per-IP rate limit on the registration endpoint (signup abuse/spam).

    Keyed on IP even for an authenticated caller, which is the case that actually
    matters: a successful signup signs the new user in, so a throttle that exempts
    authenticated requests stops applying after the very first signup.
    """

    scope = "auth-register"


class CsrfBootstrapRateThrottle(IpKeyedThrottle):
    """Per-IP rate limit on the CSRF bootstrap endpoint
    (``drf_foundation.session_auth.csrf_token``).

    Under ``CSRF_USE_SESSIONS`` the token's secret lives in the session, so serving one
    to a previously-sessionless caller writes a session row — cheap, but unbounded
    without this. Rate it generously (a legitimate client fetches one per page load, and
    again after each session rotation) and reap expired rows with ``clearsessions``.

    Keyed on IP regardless of session, since a caller that already has one can still
    walk the endpoint.
    """

    scope = "auth-csrf"


class TokenUserRateThrottle(UserRateThrottle):
    """Per-user rate limit applied only to token-authenticated requests.

    A leaked or runaway personal API token shouldn't be able to hammer the API, but
    everything else must stay unthrottled: session requests, shared-key ops requests
    (``request.auth is None``) and anonymous public reads carry no ``Token``, so all
    bypass. Throttle state lives in the default Django cache (Redis in prod, shared
    across workers).

    **The credential type is DRF's** ``rest_framework.authtoken.models.Token``,
    specifically. A project whose personal tokens are a different model (e.g. one that
    hashes them at rest rather than storing the secret as the primary key) gets no
    throttling from this class and should subclass it to widen the check — see
    ``python/README.md``. Both current consumers do exactly that, so treat the default as
    the DRF-authtoken case rather than as the general one.
    """

    scope = "token-user"

    def allow_request(self, request: Request, view: "APIView") -> bool:
        if not isinstance(request.auth, Token):
            return True
        return super().allow_request(request, view)
