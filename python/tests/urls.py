from django.http import HttpRequest, StreamingHttpResponse
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from drf_foundation import realtime, session_auth
from drf_foundation.auth import LoginView, RefreshView, logout
from drf_foundation.schemas import ok
from drf_foundation.views import health_check


@api_view(["POST"])
def protected_mutation(request: Request) -> Response:
    """A stand-in for any authenticated write — the surface CSRF has to cover."""
    return ok(None)


async def sse_stream(request: HttpRequest) -> StreamingHttpResponse:
    """An *async* view returning a stream — the only shape SSE takes under ASGI,
    and the one where releasing the DB connection is @async_unsafe."""
    return realtime.sse_response("redis://x", "events:test")


urlpatterns = [
    path("api/health", health_check, name="health-check"),
    path("api/auth/login", LoginView.as_view(), name="auth-login"),
    path("api/auth/refresh", RefreshView.as_view(), name="auth-refresh"),
    path("api/auth/logout", logout, name="auth-logout"),
    # Session auth (drf_foundation.session_auth) — the alternative to the JWT routes
    # above. A real project mounts one set or the other, not both.
    path("api/session/csrf", session_auth.csrf_token, name="session-csrf"),
    path("api/session/login", session_auth.LoginView.as_view(), name="session-login"),
    path("api/session/logout", session_auth.logout, name="session-logout"),
    path("api/session/protected", protected_mutation, name="session-protected"),
    path("api/stream", sse_stream, name="sse-stream"),
]
