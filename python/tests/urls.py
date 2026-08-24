from django.http import HttpRequest, StreamingHttpResponse
from django.urls import path
from rest_framework.decorators import api_view
from rest_framework.request import Request
from rest_framework.response import Response

from drf_foundation import realtime, session_auth
from drf_foundation.schemas import ok
from drf_foundation.views import health_check


@api_view(["POST"])
def protected_mutation(request: Request) -> Response:
    """A stand-in for any authenticated write — the surface CSRF has to cover."""
    return ok(None)


async def sse_stream(request: HttpRequest) -> StreamingHttpResponse:
    """An *async* view returning a stream — the only shape SSE takes under ASGI,
    and the one where releasing the DB connection is @async_unsafe."""
    return await realtime.asse_response("redis://x", "events:test")


# Records the concrete DatabaseWrapper the request's ORM work ran on, so a test can
# assert that *that* connection — not the test thread's — was handed back.
STREAM_DB_CONNECTIONS: list = []


async def sse_stream_after_db(request: HttpRequest) -> StreamingHttpResponse:
    """A stream from a view that touched the ORM first — the real shape, where the
    auth/tenancy lookup has already checked a connection out of the pool."""
    from asgiref.sync import sync_to_async
    from django.contrib.auth import get_user_model
    from django.db import DEFAULT_DB_ALIAS, connections

    def _work():
        get_user_model().objects.count()
        STREAM_DB_CONNECTIONS.append(connections[DEFAULT_DB_ALIAS])

    await sync_to_async(_work, thread_sensitive=True)()
    return await realtime.asse_response("redis://x", "events:test")


urlpatterns = [
    path("api/health", health_check, name="health-check"),
    # Session auth (drf_foundation.session_auth) — the package's only auth flavour
    # above. A real project mounts one set or the other, not both.
    path("api/session/csrf", session_auth.csrf_token, name="session-csrf"),
    path("api/session/login", session_auth.LoginView.as_view(), name="session-login"),
    path("api/session/logout", session_auth.logout, name="session-logout"),
    path("api/session/protected", protected_mutation, name="session-protected"),
    path("api/stream", sse_stream, name="sse-stream"),
    path("api/stream-after-db", sse_stream_after_db, name="sse-stream-after-db"),
]

# The multi-tenant MCP OAuth surface at the root (the realistic layout), and the
# single-tenant one under a prefix so both shapes are reachable in one test app.
# Route *names* collide by design — the tests address these by literal path.
from tests.mcp_fixtures import (  # noqa: E402
    MULTI_OAUTH,
    SINGLE_OAUTH,
    TEST_ENDPOINT,
)

urlpatterns += MULTI_OAUTH.urlpatterns()
urlpatterns += [
    path(f"single/{p.pattern}", p.callback, name=f"single-{p.name}")
    for p in SINGLE_OAUTH.urlpatterns()
]

# The endpoint factory, mounted the way a project would.
urlpatterns += [path("mcp", TEST_ENDPOINT, name="mcp-endpoint")]
