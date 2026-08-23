"""The MCP endpoint itself, as a view factory.

Every consumer of this package writes the same sixty lines: pull the bearer token,
resolve it to a key, answer 401 with a ``WWW-Authenticate`` header pointing at the
discovery document, build a context, hand the body to :func:`handle_post`, and put
permissive CORS on the way out. None of it is app-specific except what a context
object contains — so it lives here, and a project supplies the two callables that
genuinely vary.

The 401 is the part worth centralizing. An MCP client that receives a bare 401
gives up; one that receives ``WWW-Authenticate: Bearer resource_metadata="…"``
knows to fetch the protected-resource document and start the OAuth flow. That
header *is* the "connect" affordance, and it is easy to omit without noticing —
every manual test uses a token that already works.

CORS is permissive on this route deliberately, and only here: an MCP client's
origin is not in ``CORS_ALLOWED_ORIGINS`` and must never be added there, or the
cookie-authenticated API would start accepting it too.
"""

import json
from collections.abc import Callable
from typing import Any

from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods
from rest_framework.throttling import SimpleRateThrottle

from drf_foundation.mcp.api_keys import AbstractApiKey, TokenCodec, bearer_token, resolve_token
from drf_foundation.mcp.protocol import McpServer, handle_post


def _cors[R: HttpResponse](response: R) -> R:
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = (
        "Authorization, Content-Type, MCP-Protocol-Version, Mcp-Session-Id"
    )
    response["Access-Control-Expose-Headers"] = "WWW-Authenticate"
    response["Access-Control-Max-Age"] = "86400"
    return response


class KeyRateThrottle(SimpleRateThrottle):
    """Rate limit one MCP credential.

    Keyed on the key, not the client IP: an MCP client calls from its provider's
    egress addresses, so an IP bucket is either shared between unrelated users or
    meaningless. Construct it with the resolved key and call :meth:`allows`.

    ``scope`` must have an entry in ``DEFAULT_THROTTLE_RATES`` or DRF raises
    ``ImproperlyConfigured`` — including in a test settings module that replaces
    the whole rates dict.
    """

    def __init__(self, key: AbstractApiKey, scope: str) -> None:
        self.scope = scope
        self._ident = str(key.pk)
        super().__init__()

    def get_cache_key(self, request: Any = None, view: Any = None) -> str:
        return self.cache_format % {"scope": self.scope, "ident": self._ident}

    def allows(self) -> bool:
        """Whether this key may make one more request right now.

        ``allow_request`` wants a DRF ``Request`` and an ``APIView`` and reads
        neither — the cache key comes from the credential — and an MCP endpoint is
        a plain Django view, so there is no DRF request to hand it.
        """
        return self.allow_request(None, None)  # type: ignore[arg-type]


def mcp_endpoint(
    *,
    server: McpServer | Callable[[], McpServer],
    key_model: type[AbstractApiKey],
    codec: TokenCodec,
    context: Callable[[Any], Any],
    issuer: Callable[[], str],
    mcp_path: str = "/mcp",
    realm: str = "MCP",
    select_related: tuple[str, ...] = (),
    refuse: Callable[[Any], str | None] | None = None,
    throttle_scope: str | None = None,
) -> Callable[[HttpRequest], HttpResponse]:
    """Build the ``/mcp`` view.

    ``server`` may be an :class:`McpServer` or a callable returning one (handy when
    the registry is assembled lazily). ``context`` receives the resolved key row and
    returns whatever the tool handlers should see — this package never inspects it.

    ``refuse`` is the project's extra credential check beyond "the key exists and is
    not revoked": return a message to reject with 401, or ``None`` to allow. The
    usual case is a deactivated account, which the key itself knows nothing about.

    ``throttle_scope`` names a ``DEFAULT_THROTTLE_RATES`` entry; omit it for no
    per-key limit.
    """

    def resolve_server() -> McpServer:
        return server() if callable(server) else server

    def unauthorized(detail: str) -> HttpResponse:
        response = JsonResponse({"error": "invalid_token", "error_description": detail}, status=401)
        metadata = f"{issuer().rstrip('/')}/.well-known/oauth-protected-resource"
        response["WWW-Authenticate"] = f'Bearer realm="{realm}", resource_metadata="{metadata}"'
        return _cors(response)

    @csrf_exempt
    @require_http_methods(["POST", "OPTIONS"])
    def view(request: HttpRequest) -> HttpResponse:
        if request.method == "OPTIONS":
            return _cors(HttpResponse(status=204))

        token = bearer_token(request)
        if token is None:
            return unauthorized("Send an MCP access token as a Bearer credential.")

        key = resolve_token(key_model, codec, token, select_related=select_related)
        if key is None:
            return unauthorized("This access token is unknown, revoked, or expired.")

        if refuse is not None:
            reason = refuse(key)
            if reason:
                return unauthorized(reason)

        if throttle_scope is not None and not KeyRateThrottle(key, throttle_scope).allows():
            return _cors(
                JsonResponse(
                    {
                        "error": "rate_limited",
                        "error_description": (
                            "Too many MCP requests for this connection — retry shortly."
                        ),
                    },
                    status=429,
                )
            )

        status, body = handle_post(
            resolve_server(),
            context(key),
            request.body,
            request.headers.get("MCP-Protocol-Version"),
        )
        if body is None:
            return _cors(HttpResponse(status=status))
        return _cors(HttpResponse(json.dumps(body), status=status, content_type="application/json"))

    view.__name__ = "mcp_endpoint"
    view.__doc__ = f"MCP streamable-HTTP endpoint (mounted at {mcp_path})."
    return view
