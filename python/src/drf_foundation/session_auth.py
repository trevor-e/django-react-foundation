"""Session-cookie auth wire contract for browser clients (no extra dependencies).

This package's only auth flavour, and the right one when the browser client is a
first-party SPA on the same site as the API. The credential is Django's ``sessionid``
cookie: ``HttpOnly``, so page JavaScript cannot read (or leak) it, with revocation as a
session-row delete. It costs CSRF handling, which DRF's ``SessionAuthentication``
enforces for you, and it rules out cross-*site* frontends, which never receive the
cookie.

A ``drf_foundation.auth`` module used to ship a simplejwt pair for that cross-site case.
It was removed once both consumers moved to cookies and it had no importers left. The
frontend package still supports JWT mode in its apiClient, so a cross-site or native
client remains possible — it brings its own backend views rather than importing them
from here.

**Status-code gotcha, worth knowing before you wire this up.** DRF takes the status for
an unauthenticated request from the *first* entry in ``DEFAULT_AUTHENTICATION_CLASSES``,
via that authenticator's ``authenticate_header()``. Stock ``SessionAuthentication``
returns ``None`` there, so a session-only stack answers ``403``, not ``401`` — and an SPA
that keys "signed out" on 401 will keep rendering as though the user were logged in. Lead
the list with a header authenticator (an API-key or token class) and 401 comes for free;
otherwise subclass ``SessionAuthentication`` and override ``authenticate_header``.

Wire it up::

    # settings.py
    REST_FRAMEWORK = {
        "DEFAULT_AUTHENTICATION_CLASSES": [
            "rest_framework.authentication.SessionAuthentication",
        ],
    }
    globals().update(session_auth_settings(cross_origin_spa=True))  # settings_helpers

    # accounts/urls.py
    from drf_foundation.session_auth import LoginView, csrf_token, logout

    urlpatterns = [
        path("auth/csrf", csrf_token, name="auth-csrf"),
        path("auth/login", LoginView.as_view(), name="auth-login"),
        path("auth/logout", logout, name="auth-logout"),
    ]

Registration stays project code (verification emails, invites, extra fields) — call
:func:`start_session` after creating the user to sign them in.

**The CSRF contract**, which the ``react-vite-foundation`` apiClient's session mode
speaks: the token is delivered as a *value* (``{"csrf_token": ...}``), never as a
readable cookie, and echoed back in the ``X-CSRFToken`` header on unsafe methods. With
``CSRF_USE_SESSIONS`` the secret lives in the session, so a project can ship with exactly
one cookie and none readable by JavaScript. ``django.contrib.auth.login``/``logout``
rotate the token, so every endpoint that starts or ends a session returns the fresh one
rather than making the client re-fetch it.
"""

from django.contrib.auth import authenticate
from django.contrib.auth import login as django_login
from django.contrib.auth import logout as django_logout
from django.middleware.csrf import get_token
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_protect
from rest_framework import status as http_status
from rest_framework.decorators import api_view, permission_classes, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView

from drf_foundation.schemas import Schema, err, ok, parse
from drf_foundation.throttling import CsrfBootstrapRateThrottle, LoginRateThrottle


class LoginRequest(Schema):
    """Default login body. Projects that bound their request fields (recommended)
    subclass this, add their limits, and point ``LoginView.request_schema`` at it."""

    email: str
    password: str


class SessionResult(Schema):
    """What every session-starting endpoint returns.

    ``csrf_token`` is the post-rotation token for the new session — the client stores it
    and sends it as ``X-CSRFToken``. It is not a credential: it is worthless without the
    session cookie, which is exactly why it is safe to hand to page JavaScript.
    """

    csrf_token: str


class LogoutResult(Schema):
    detail: str = "Logged out."


def start_session(request: Request, user: object) -> SessionResult:
    """Sign ``user`` in on ``request``'s session and return the rotated CSRF token.

    ``django.contrib.auth.login`` cycles the session key (session-fixation defence) and
    rotates the CSRF token; ``get_token`` after it returns the new one.
    """
    django_login(request, user)  # pyrefly: ignore[bad-argument-type]
    return SessionResult(csrf_token=get_token(request))


@method_decorator(csrf_protect, name="dispatch")
class LoginView(APIView):
    """``POST /api/auth/login`` — email + password in, session cookie out.

    CSRF-protected despite being anonymous: DRF exempts every ``APIView`` from
    ``CsrfViewMiddleware`` and re-applies the check inside ``SessionAuthentication``,
    which no-ops for an anonymous caller. Without ``csrf_protect`` here, login is the one
    unprotected mutation — and login CSRF (silently signing a victim into the attacker's
    account) is a real attack. Clients fetch a token from :func:`csrf_token` first; the
    apiClient's session mode does that automatically on a missing token.

    Projects with their own throttle scopes or bounded request schema subclass this::

        class MyLogin(LoginView):
            request_schema = MyLoginRequest
            throttle_classes = (MyLoginRateThrottle,)
    """

    permission_classes = (AllowAny,)
    throttle_classes = (LoginRateThrottle,)
    request_schema: type[LoginRequest] = LoginRequest

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        data = parse(request, self.request_schema)
        # `authenticate` reads the credential under the user model's USERNAME_FIELD, so
        # `username=` is correct even for an email-as-identifier user model. It also
        # rejects inactive users (ModelBackend.user_can_authenticate).
        user = authenticate(request, username=data.email, password=data.password)
        if user is None:
            return err(
                "No active account found with the given credentials.",
                http_status.HTTP_401_UNAUTHORIZED,
            )
        return ok(start_session(request, user))


@api_view(["POST"])
@permission_classes([AllowAny])
def logout(request: Request) -> Response:
    """``POST /api/auth/logout`` — flush the session, clear the cookie, no body.

    Deliberately forgiving and anonymous-safe: the user's intent is "end my session", so
    an already-expired or absent session is success, not an error. ``django_logout`` is a
    no-op flush for an anonymous request.
    """
    django_logout(request)  # pyrefly: ignore[bad-argument-type]
    return ok(LogoutResult())


@api_view(["GET"])
@permission_classes([AllowAny])
@throttle_classes([CsrfBootstrapRateThrottle])
def csrf_token(request: Request) -> Response:
    """``GET /api/auth/csrf`` — hand the caller its CSRF token as a value.

    The bootstrap a cross-origin SPA needs before its first unsafe request: under
    ``CSRF_USE_SESSIONS`` there is no readable cookie to lift the token from, and under a
    cookie-stored token the SPA's origin differs from the API's, so it still cannot read
    it. Throttled per IP because, with session-stored CSRF, each call to a
    previously-sessionless client creates a session row.
    """
    return ok(SessionResult(csrf_token=get_token(request)))
