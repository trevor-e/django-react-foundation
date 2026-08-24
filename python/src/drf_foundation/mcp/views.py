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
from drf_foundation.mcp.protocol import INVALID_REQUEST, McpServer, _error, handle_post


def _cors[R: HttpResponse](response: R) -> R:
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = (
        "Authorization, Content-Type, MCP-Protocol-Version, Mcp-Session-Id"
    )
    response["Access-Control-Expose-Headers"] = (
        "WWW-Authenticate, Retry-After, X-RateLimit-Limit, X-RateLimit-Remaining, X-RateLimit-Reset"
    )
    response["Access-Control-Max-Age"] = "86400"
    return response


def _stamp[R: HttpResponse](response: R, headers: dict[str, str]) -> R:
    for name, value in headers.items():
        response[name] = value
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

    def headers(self) -> dict[str, str]:
        """``X-RateLimit-*`` describing this key's bucket, after :meth:`allows`.

        An agent that can read its remaining budget paces itself; one that cannot
        finds the limit by hitting it, and a 429 mid-conversation is indistinguishable
        from the tool being broken. That is why these ride *every* response, not just
        the 429 — by then it is too late to be useful.

        ``allow_request`` leaves ``history`` holding this request on success and a
        full window on refusal, so ``len(history)`` is the count either way and the
        oldest entry is when a slot next frees.

        Empty when the scope is switched off — ``DEFAULT_THROTTLE_RATES[scope] = None``
        is the DRF idiom for that, and the one test settings usually reach for. DRF
        short-circuits ``allow_request`` before recording any history in that case, so
        there is no budget to report and reporting a fabricated one would be worse.
        """
        history = getattr(self, "history", None)
        if self.rate is None or history is None:
            return {}
        oldest = history[-1] if history else self.now
        return {
            "X-RateLimit-Limit": str(self.num_requests),
            "X-RateLimit-Remaining": str(max(0, self.num_requests - len(history))),
            "X-RateLimit-Reset": str(int(oldest + self.duration)),
        }


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
    max_body_bytes: int | None = None,
) -> Callable[[HttpRequest], HttpResponse]:
    """Build the ``/mcp`` view.

    ``server`` may be an :class:`McpServer` or a callable returning one (handy when
    the registry is assembled lazily). ``context`` receives the resolved key row and
    returns whatever the tool handlers should see — this package never inspects it.

    ``refuse`` is the project's extra credential check beyond "the key exists and is
    not revoked": return a message to reject with 401, or ``None`` to allow. The
    usual case is a deactivated account, which the key itself knows nothing about.

    ``throttle_scope`` names a ``DEFAULT_THROTTLE_RATES`` entry; omit it for no
    per-key limit. When set, every response carries ``X-RateLimit-*`` so a client
    can pace itself rather than discover the ceiling by hitting it.

    ``max_body_bytes`` caps the request body. A tools-only JSON-RPC call is small,
    and without a ceiling an unauthenticated-shaped request still costs a full read
    into memory before the token is even checked. ``None`` disables the cap, which
    leaves ``DATA_UPLOAD_MAX_MEMORY_SIZE`` as the only backstop.
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

        budget: dict[str, str] = {}
        if throttle_scope is not None:
            throttle = KeyRateThrottle(key, throttle_scope)
            allowed = throttle.allows()
            budget = throttle.headers()
            if not allowed:
                response = JsonResponse(
                    {
                        "error": "rate_limited",
                        "error_description": (
                            "Too many MCP requests for this connection — retry shortly."
                        ),
                    },
                    status=429,
                )
                wait = throttle.wait()
                if wait is not None:
                    response["Retry-After"] = str(int(wait) + 1)
                return _cors(_stamp(response, budget))

        raw = request.body
        if max_body_bytes is not None and len(raw) > max_body_bytes:
            # A JSON-RPC envelope, not a bare 413 body: the caller is an MCP client,
            # and an unparseable response reads to it as the server being broken.
            return _cors(
                _stamp(
                    JsonResponse(
                        _error(None, INVALID_REQUEST, "Request body too large."), status=413
                    ),
                    budget,
                )
            )

        status, body = handle_post(
            resolve_server(),
            context(key),
            raw,
            request.headers.get("MCP-Protocol-Version"),
        )
        if body is None:
            return _cors(_stamp(HttpResponse(status=status), budget))
        return _cors(
            _stamp(
                HttpResponse(json.dumps(body), status=status, content_type="application/json"),
                budget,
            )
        )

    view.__name__ = "mcp_endpoint"
    view.__doc__ = f"MCP streamable-HTTP endpoint (mounted at {mcp_path})."
    return view
