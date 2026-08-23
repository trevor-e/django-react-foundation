"""The OAuth 2.1 subset an MCP client needs to connect, and nothing more.

claude.ai's custom-connector UI takes a URL and offers no header field, so
"connect my app to Claude" is an OAuth problem whether or not the app wanted to
be an authorization server. This is the minimal surface that satisfies it:
authorization-code grant with mandatory PKCE (S256 only), public clients via open
dynamic registration, no client secrets, no refresh tokens.

**What a project supplies is identity and minting, never the handshake.** An
:class:`OAuthProvider` answers three questions — what may this user connect, may
they connect *this*, and what credential does that produce — and the package does
everything else. Nothing about PKCE, redirect matching, code single-use, or token
hashing is overridable, because those are exactly the things that are silently
wrong when each app reimplements them.

Tenancy stays the project's. A "resource" is whatever a token acts on: a
multi-tenant app returns one per tenant the user belongs to and the consent page
renders a picker; a single-tenant app returns the user themselves and it renders
none. Same flow, same template, no branch in this module.

These are plain Django views, not DRF: the JSON endpoints are CSRF-exempt
bearer-style routes with hand-set permissive CORS (an MCP client's origin is not
in ``CORS_ALLOWED_ORIGINS`` and must not be, or the cookie-authed API would
accept it too), while the consent pages ride the session cookie and stay
same-origin and CSRF-protected.
"""

import base64
import hashlib
import json
import secrets
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any, Protocol, runtime_checkable
from urllib.parse import quote, urlencode, urlsplit

from django.conf import settings
from django.core import signing
from django.db import models
from django.http import HttpRequest, HttpResponse, HttpResponseRedirect, JsonResponse
from django.shortcuts import render
from django.urls import URLPattern, path
from django.utils import timezone
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_http_methods

from drf_foundation.mcp.api_keys import TokenCodec

CONSENT_SALT = "drf_foundation.mcp.oauth.consent"
CONSENT_MAX_AGE_SECONDS = 15 * 60
CODE_LIFETIME_SECONDS = 10 * 60
MAX_REDIRECT_URIS = 10
MAX_BODY_BYTES = 16_384
MAX_REDIRECT_URI_LENGTH = 500


# --- the seam ----------------------------------------------------------------


@dataclass(frozen=True)
class Resource:
    """Something a token can be granted against, as shown on the consent page.

    ``obj`` is the project's own row; the package passes it back untouched.
    """

    id: str
    label: str
    obj: Any = None


@dataclass(frozen=True)
class Scope:
    """One access level a user can consent to, in words they can act on."""

    value: str
    label: str
    description: str = ""


class MintRefused(Exception):
    """The provider declined to mint — a quota, a policy, a disabled account.

    The message is shown to the client verbatim, so write it for the person who
    will read it in a connection dialog, and say what would unblock them.
    """


@runtime_checkable
class OAuthProvider(Protocol):
    """Identity and minting. Everything else in this module is not yours to change.

    ``resolve`` is the authorization check — the one place a project can get this
    wrong, so it has no default: return ``None`` whenever the user has no claim on
    the resource, and the flow fails with ``access_denied`` before anything is
    minted.
    """

    @property
    def scopes(self) -> Sequence[Scope]:
        """The access levels offered on the consent page, narrowest first.

        A read-only property, not a plain attribute: an implementation should be
        free to declare it as a tuple class attribute without a type checker
        calling that an inconsistent override.
        """
        ...

    def resources(self, user: Any) -> list[Resource]:
        """Everything ``user`` may connect. One entry renders no picker."""
        ...

    def resolve(self, user: Any, resource_id: str) -> Resource | None:
        """The resource, if this user may grant against it. ``None`` denies."""
        ...

    def mint(self, *, user: Any, resource: Resource, scope: str, client: Any) -> tuple[str, Any]:
        """Create a credential. Returns ``(secret, key_row)``; raises
        :class:`MintRefused` to decline."""
        ...

    def replace_previous(self, *, user: Any, resource: Resource, client: Any) -> None:
        """Revoke this client's existing keys for this resource, before minting."""
        ...

    def revoke(self, key: Any, *, reason: str) -> None:
        """Revoke one key — used when a replayed code has to undo its own grant."""
        ...


_REQUIRED_PROVIDER_METHODS = ("resources", "resolve", "mint", "replace_previous", "revoke")


# --- configuration -----------------------------------------------------------


@dataclass(frozen=True)
class OAuthModels:
    """The project's concrete tables. See :mod:`drf_foundation.mcp.models`.

    ``resource_field`` names the FK the project declared for whatever a token
    grants access to. It is configurable because an app that already calls it
    ``household`` or ``workspace`` should not have to rename a live column — and
    because reading better at the call site is worth more than a fixed name here.
    """

    client: type[models.Model]
    code: type[models.Model]
    grant: type[models.Model]
    resource_field: str = "resource"


@dataclass(frozen=True)
class OAuthConfig:
    """Everything about the deployment that is not the flow itself.

    ``issuer`` must be an https origin in production — the discovery documents
    embed it, and an MCP client will happily follow whatever they say.
    """

    issuer: Callable[[], str]
    resource_name: str
    codec: TokenCodec
    login_url: Callable[[HttpRequest], str]
    mcp_path: str = "/mcp"
    consent_template: str = "drf_foundation/mcp/consent.html"
    error_template: str = "drf_foundation/mcp/oauth_error.html"
    register_throttle: type | None = None
    token_throttle: type | None = None
    client_id_prefix: str = "mcpc_"
    # Shown when the signed-in user has nothing to grant against. Worth overriding:
    # the generic wording cannot name what they are missing or how to get one.
    no_resources_title: str = "Nothing to connect yet"
    no_resources_message: str = (
        "There's nothing on this account to connect. Set one up first, then retry the connection."
    )
    extra_consent_context: Callable[[HttpRequest], dict[str, Any]] = field(
        default_factory=lambda: lambda request: {}
    )


# --- helpers -----------------------------------------------------------------


def _cors[R: HttpResponse](response: R) -> R:
    response["Access-Control-Allow-Origin"] = "*"
    response["Access-Control-Allow-Methods"] = "GET, POST, OPTIONS"
    response["Access-Control-Allow-Headers"] = (
        "Authorization, Content-Type, MCP-Protocol-Version, Mcp-Session-Id"
    )
    response["Access-Control-Expose-Headers"] = "WWW-Authenticate"
    response["Access-Control-Max-Age"] = "86400"
    return response


#: Public alias — an MCP endpoint view needs the same headers on a bearer-only
#: route, and should not reach for the underscore-prefixed name to get them.
permissive_cors = _cors


def _preflight() -> HttpResponse:
    return _cors(HttpResponse(status=204))


def _oauth_error(error: str, description: str, status: int = 400) -> JsonResponse:
    return _cors(JsonResponse({"error": error, "error_description": description}, status=status))


def _is_loopback(url: str) -> bool:
    parts = urlsplit(url)
    return parts.scheme == "http" and parts.hostname in ("localhost", "127.0.0.1", "::1")


def _valid_registration_uri(uri: str) -> bool:
    if not uri or len(uri) > MAX_REDIRECT_URI_LENGTH:
        return False
    parts = urlsplit(uri)
    if parts.fragment:
        return False
    if parts.scheme == "https" and parts.hostname:
        return True
    return _is_loopback(uri)


def redirect_uri_allowed(candidate: str, registered: list[str]) -> bool:
    """Exact match, except loopback redirects compare ignoring the port.

    RFC 8252 §7.3: a native client binds an ephemeral port per session, so pinning
    the port would break every second connection. Everything else about the URI
    still has to match exactly.
    """
    if candidate in registered:
        return True
    if not _is_loopback(candidate):
        return False
    cand = urlsplit(candidate)
    for entry in registered:
        if not _is_loopback(entry):
            continue
        reg = urlsplit(entry)
        if (
            cand.scheme == reg.scheme
            and cand.hostname == reg.hostname
            and cand.path == reg.path
            and cand.query == reg.query
        ):
            return True
    return False


def _pkce_matches(verifier: str, challenge: str) -> bool:
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return constant_time_compare(computed, challenge)


def _redirect_back(redirect_uri: str, params: dict[str, str]) -> HttpResponseRedirect:
    separator = "&" if "?" in redirect_uri else "?"
    return HttpResponseRedirect(f"{redirect_uri}{separator}{urlencode(params)}")


def _consent_csp(redirect_uri: str) -> dict[str, list[str]] | None:
    """CSP for the consent *document*, widened to the one registered redirect.

    Chrome (unlike Firefox and Safari) enforces ``form-action`` across a form
    submission's entire redirect chain, so a global ``form-action 'self'`` strands
    the user on Approve: the 302 to the client's callback is blocked client-side
    while the code has already been issued. Widening to ``'self'`` plus that one
    origin keeps the directive meaningful instead of dropping it.
    """
    configured = getattr(settings, "SECURE_CSP", None)
    if not configured:
        return None
    from django.utils.csp import CSP

    parts = urlsplit(redirect_uri)
    config = dict(configured)
    config["form-action"] = [CSP.SELF, f"{parts.scheme}://{parts.netloc}"]
    return config


# --- the server --------------------------------------------------------------


class McpOAuth:
    """The authorization server: discovery, registration, consent, token exchange.

    Build one at import time and hand it to :meth:`urlpatterns`; a provider that
    is missing a required method fails here rather than on somebody's first
    connection attempt.
    """

    def __init__(
        self, *, provider: OAuthProvider, models: OAuthModels, config: OAuthConfig
    ) -> None:
        missing = [
            name
            for name in _REQUIRED_PROVIDER_METHODS
            if not callable(getattr(provider, name, None))
        ]
        if missing:
            raise TypeError(
                f"{type(provider).__name__} is not a complete OAuthProvider — "
                f"missing: {', '.join(missing)}. "
                "resolve() in particular has no default: it is the authorization "
                "check, and a permissive fallback would hand out tokens for "
                "resources the consenting user cannot reach."
            )
        if not getattr(provider, "scopes", None):
            raise TypeError(f"{type(provider).__name__}.scopes must list at least one Scope.")
        self.provider = provider
        self.models = models
        self.config = config

    # -- small accessors --

    @property
    def issuer(self) -> str:
        return self.config.issuer().rstrip("/")

    @property
    def scope_values(self) -> list[str]:
        return [scope.value for scope in self.provider.scopes]

    def default_scope(self, requested: str) -> str:
        """The scope pre-selected on the consent page.

        Honor a narrower request, otherwise offer the broadest scope — the client
        asked to be useful, and the person is about to see exactly what they are
        granting either way.
        """
        asked = set(requested.split())
        for scope in self.provider.scopes:
            if asked == {scope.value}:
                return scope.value
        return self.provider.scopes[-1].value

    def _error_page(
        self, request: HttpRequest, title: str, message: str, status: int = 400
    ) -> HttpResponse:
        return render(
            request,
            self.config.error_template,
            {"title": title, "message": message},
            status=status,
        )

    def _throttled(self, request: HttpRequest, throttle_cls: type | None) -> JsonResponse | None:
        if throttle_cls is None:
            return None
        throttle = throttle_cls()
        # SimpleRateThrottle only reads request.META, so a plain HttpRequest works
        # even though the signature wants a DRF Request (these are plain Django
        # views by design).
        if throttle.allow_request(request, None):
            return None
        return _oauth_error(
            "temporarily_unavailable", "Rate limit exceeded — retry shortly.", status=429
        )

    # -- discovery --

    def protected_resource_metadata(self, request: HttpRequest) -> HttpResponse:
        if request.method == "OPTIONS":
            return _preflight()
        response = JsonResponse(
            {
                "resource": f"{self.issuer}{self.config.mcp_path}",
                "authorization_servers": [self.issuer],
                "scopes_supported": self.scope_values,
                "bearer_methods_supported": ["header"],
                "resource_name": self.config.resource_name,
            }
        )
        response["Cache-Control"] = "public, max-age=3600"
        return _cors(response)

    def authorization_server_metadata(self, request: HttpRequest) -> HttpResponse:
        if request.method == "OPTIONS":
            return _preflight()
        base = self.issuer
        response = JsonResponse(
            {
                "issuer": base,
                "authorization_endpoint": f"{base}/oauth/authorize",
                "token_endpoint": f"{base}/oauth/token",
                "registration_endpoint": f"{base}/oauth/register",
                "response_types_supported": ["code"],
                "grant_types_supported": ["authorization_code"],
                "code_challenge_methods_supported": ["S256"],
                "token_endpoint_auth_methods_supported": ["none"],
                "scopes_supported": self.scope_values,
            }
        )
        response["Cache-Control"] = "public, max-age=3600"
        return _cors(response)

    # -- dynamic client registration (RFC 7591) --

    def register(self, request: HttpRequest) -> HttpResponse:
        if request.method == "OPTIONS":
            return _preflight()
        throttled = self._throttled(request, self.config.register_throttle)
        if throttled is not None:
            return throttled
        if len(request.body) > MAX_BODY_BYTES:
            return _oauth_error("invalid_client_metadata", "Registration payload too large.")
        try:
            payload = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return _oauth_error("invalid_client_metadata", "Body must be JSON (RFC 7591).")
        if not isinstance(payload, dict):
            return _oauth_error("invalid_client_metadata", "Body must be a JSON object.")

        redirect_uris = payload.get("redirect_uris")
        if (
            not isinstance(redirect_uris, list)
            or not redirect_uris
            or len(redirect_uris) > MAX_REDIRECT_URIS
            or not all(isinstance(u, str) and _valid_registration_uri(u) for u in redirect_uris)
        ):
            return _oauth_error(
                "invalid_redirect_uri",
                f"redirect_uris must be 1-{MAX_REDIRECT_URIS} https URLs "
                "(http allowed for localhost only), without fragments.",
            )
        name = payload.get("client_name") or "Connected app"
        if not isinstance(name, str):
            return _oauth_error("invalid_client_metadata", "client_name must be a string.")

        client = self.models.client.objects.create(
            client_id=f"{self.config.client_id_prefix}{secrets.token_urlsafe(24)}",
            name=name.strip()[:100] or "Connected app",
            redirect_uris=redirect_uris,
        )
        return _cors(
            JsonResponse(
                {
                    "client_id": client.client_id,
                    "client_name": client.name,
                    "redirect_uris": client.redirect_uris,
                    "token_endpoint_auth_method": "none",
                    "grant_types": ["authorization_code"],
                    "response_types": ["code"],
                    "client_id_issued_at": int(client.created_at.timestamp()),
                },
                status=201,
            )
        )

    # -- authorization + consent --

    def authorize(self, request: HttpRequest) -> HttpResponse:
        if request.method == "GET":
            return self._authorize_start(request)
        return self._consent_submit(request)

    def _authorize_start(self, request: HttpRequest) -> HttpResponse:
        params = request.GET
        client = self.models.client.objects.filter(client_id=params.get("client_id", "")).first()
        if client is None:
            return self._error_page(
                request,
                "Unknown app",
                "This connection request came from an unregistered app.",
            )
        redirect_uri = params.get("redirect_uri", "")
        if not redirect_uri or not redirect_uri_allowed(redirect_uri, client.redirect_uris):
            # Never redirect to an unregistered target — render, don't bounce.
            return self._error_page(
                request,
                "Invalid redirect",
                "The app supplied a return address it didn't register.",
            )

        state = params.get("state", "")

        def fail(error: str, description: str) -> HttpResponse:
            redirect_params = {"error": error, "error_description": description}
            if state:
                redirect_params["state"] = state
            return _redirect_back(redirect_uri, redirect_params)

        if params.get("response_type") != "code":
            return fail("unsupported_response_type", "Only response_type=code is supported.")
        code_challenge = params.get("code_challenge", "")
        if not code_challenge or len(code_challenge) > 128:
            return fail("invalid_request", "PKCE (S256) is required.")
        if params.get("code_challenge_method", "S256") != "S256":
            return fail("invalid_request", "Only the S256 code_challenge_method is supported.")

        if not request.user.is_authenticated:
            return HttpResponseRedirect(self.config.login_url(request))

        resources = self.provider.resources(request.user)
        if not resources:
            return self._error_page(
                request,
                self.config.no_resources_title,
                self.config.no_resources_message,
            )

        context = {
            "client_name": client.name,
            "redirect_host": urlsplit(redirect_uri).hostname or redirect_uri,
            "loopback_only": all(_is_loopback(u) for u in client.redirect_uris),
            "resources": resources,
            "single_resource": len(resources) == 1,
            "scopes": list(self.provider.scopes),
            "default_scope": self.default_scope(params.get("scope", "")),
            "payload": signing.dumps(
                {
                    "c": client.client_id,
                    "r": redirect_uri,
                    "s": state,
                    "ch": code_challenge,
                },
                salt=CONSENT_SALT,
            ),
            **self.config.extra_consent_context(request),
        }
        response = render(request, self.config.consent_template, context)
        csp = _consent_csp(redirect_uri)
        if csp is not None:
            # Same mechanism as django.views.decorators.csp.csp_override, but the
            # config is per-request (the redirect origin), so the decorator can't
            # express it. setattr because the attribute is the middleware's dynamic
            # contract, not a declared HttpResponse field.
            setattr(response, "_csp_config", csp)  # noqa: B010
        return response

    def _consent_submit(self, request: HttpRequest) -> HttpResponse:
        if not request.user.is_authenticated:
            return self._error_page(
                request, "Signed out", "Your session expired — retry from the app.", 403
            )
        try:
            payload = signing.loads(
                request.POST.get("payload", ""),
                salt=CONSENT_SALT,
                max_age=CONSENT_MAX_AGE_SECONDS,
            )
        except signing.BadSignature:
            return self._error_page(
                request,
                "Expired",
                "This consent form expired — retry the connection from the app.",
            )
        client = self.models.client.objects.filter(client_id=payload["c"]).first()
        redirect_uri = payload["r"]
        if client is None or not redirect_uri_allowed(redirect_uri, client.redirect_uris):
            return self._error_page(
                request, "Unknown app", "This app registration no longer exists."
            )

        state = payload["s"]
        if request.POST.get("action") != "approve":
            params = {"error": "access_denied", "error_description": "The user declined."}
            if state:
                params["state"] = state
            return _redirect_back(redirect_uri, params)

        resource = self.provider.resolve(request.user, request.POST.get("resource", ""))
        if resource is None:
            return self._error_page(
                request, "Not yours to connect", "Pick something you have access to.", 403
            )
        scope = request.POST.get("scope", "")
        if scope not in self.scope_values:
            return self._error_page(
                request, "Invalid scope", "Pick one of the offered access levels."
            )

        code = secrets.token_urlsafe(43)
        self.models.code.objects.create(
            client=client,
            code_hash=self.config.codec.hash(code),
            redirect_uri=redirect_uri,
            code_challenge=payload["ch"],
            user=request.user,
            scope=scope,
            expires_at=timezone.now() + timedelta(seconds=CODE_LIFETIME_SECONDS),
            **{self.models.resource_field: resource.obj},
        )
        params = {"code": code}
        if state:
            params["state"] = state
        return _redirect_back(redirect_uri, params)

    # -- token exchange --

    def token(self, request: HttpRequest) -> HttpResponse:
        if request.method == "OPTIONS":
            return _preflight()
        throttled = self._throttled(request, self.config.token_throttle)
        if throttled is not None:
            return throttled

        form = request.POST  # RFC 6749: application/x-www-form-urlencoded
        if form.get("grant_type") != "authorization_code":
            return _oauth_error(
                "unsupported_grant_type", "Only the authorization_code grant is supported."
            )
        code = form.get("code", "")
        client_id = form.get("client_id", "")
        verifier = form.get("code_verifier", "")
        redirect_uri = form.get("redirect_uri", "")
        if not code or not client_id or not verifier or not redirect_uri:
            return _oauth_error(
                "invalid_request",
                "code, client_id, code_verifier, and redirect_uri are all required.",
            )

        client = self.models.client.objects.filter(client_id=client_id).first()
        if client is None:
            return _oauth_error("invalid_client", "Unknown client.", status=401)
        resource_field = self.models.resource_field
        auth_code = (
            self.models.code.objects.filter(code_hash=self.config.codec.hash(code))
            .select_related("client", resource_field, "user", "issued_key")
            .first()
        )
        if auth_code is None or auth_code.client_id != client.id:
            return _oauth_error("invalid_grant", "Unknown or mismatched authorization code.")
        if auth_code.used_at is not None:
            # OAuth 2.1: a replayed code revokes what it minted.
            if auth_code.issued_key is not None and auth_code.issued_key.is_active:
                self.provider.revoke(auth_code.issued_key, reason="code_replayed")
            return _oauth_error("invalid_grant", "This authorization code was already used.")
        if auth_code.expires_at < timezone.now():
            return _oauth_error("invalid_grant", "This authorization code expired.")
        if auth_code.redirect_uri != redirect_uri:
            return _oauth_error("invalid_grant", "redirect_uri does not match the authorization.")
        if not _pkce_matches(verifier, auth_code.code_challenge):
            return _oauth_error("invalid_grant", "PKCE verification failed.")

        resource_obj = getattr(auth_code, resource_field)
        resource = Resource(
            id=str(getattr(auth_code, f"{resource_field}_id")),
            label=str(resource_obj),
            obj=resource_obj,
        )
        self.provider.replace_previous(user=auth_code.user, resource=resource, client=client)
        try:
            secret, key = self.provider.mint(
                user=auth_code.user, resource=resource, scope=auth_code.scope, client=client
            )
        except MintRefused as exc:
            return _oauth_error("invalid_request", str(exc))

        now = timezone.now()
        self.models.grant.objects.create(
            client=client, api_key=key, **{resource_field: resource_obj}
        )
        auth_code.used_at = now
        auth_code.issued_key = key
        auth_code.save(update_fields=["used_at", "issued_key"])
        client.last_grant_at = now
        client.save(update_fields=["last_grant_at"])
        return _cors(
            JsonResponse({"access_token": secret, "token_type": "Bearer", "scope": auth_code.scope})
        )

    # -- wiring --

    def urlpatterns(self, *, mcp_view: Any = None, mcp_route: str = "mcp") -> list[URLPattern]:
        """The routes, mounted at the site root (they are not under ``/api/``).

        Pass ``mcp_view`` to have the MCP endpoint itself routed here too, so the
        whole surface is one include and the discovery documents cannot drift from
        where the endpoint actually lives.
        """
        prm = require_http_methods(["GET", "OPTIONS"])(self.protected_resource_metadata)
        routes = [
            path(
                ".well-known/oauth-protected-resource",
                prm,
                name="mcp-protected-resource",
            ),
            # claude.ai probes the path-suffixed spelling too (RFC 9728 §3.1).
            path(
                f".well-known/oauth-protected-resource/{mcp_route}",
                prm,
                name="mcp-protected-resource-suffixed",
            ),
            path(
                ".well-known/oauth-authorization-server",
                require_http_methods(["GET", "OPTIONS"])(self.authorization_server_metadata),
                name="oauth-metadata",
            ),
            path(
                "oauth/register",
                csrf_exempt(require_http_methods(["POST", "OPTIONS"])(self.register)),
                name="oauth-register",
            ),
            path(
                "oauth/authorize",
                require_http_methods(["GET", "POST"])(self.authorize),
                name="oauth-authorize",
            ),
            path(
                "oauth/token",
                csrf_exempt(require_http_methods(["POST", "OPTIONS"])(self.token)),
                name="oauth-token",
            ),
        ]
        if mcp_view is not None:
            routes.insert(0, path(mcp_route, mcp_view, name="mcp-endpoint"))
        return routes


def login_redirect(frontend_base_url: str) -> Callable[[HttpRequest], str]:
    """A ``login_url`` that bounces to an SPA's login page and back again."""

    def build(request: HttpRequest) -> str:
        target = quote(request.build_absolute_uri(), safe="")
        return f"{frontend_base_url.rstrip('/')}/login?next={target}"

    return build
