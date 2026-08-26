"""Client ID Metadata Documents — a URL as the client_id, fetched and verified.

The mechanism (draft-ietf-oauth-client-id-metadata-document, adopted by the MCP
authorization spec): a client identifies itself with an HTTPS URL it controls,
the document at that URL carries the metadata dynamic registration would have,
and the origin of the URL becomes an identity the consent page can actually
vouch for — "claude.ai", not whatever a register call typed into ``client_name``.

This module is the OAuth surface's **only outbound request**, made with a URL
supplied by an unauthenticated stranger, so the fetcher is deliberately paranoid:

- every resolved address must be public — private, loopback, link-local,
  multicast and reserved ranges are refused *before* connecting, and the
  connection goes to the vetted address rather than re-resolving the name
  (a second lookup is what DNS-rebinding attacks race);
- redirects are never followed, responses are size-capped, connections are
  time-capped;
- results (including failures) are cached briefly, and fetches share one global
  per-minute budget, so the authorize endpoint cannot be used as a fetch pump.

Built on the stdlib rather than an HTTP client dependency: address pinning is
the point, and it is easier to do by hand than to retrofit onto a pool.

The seam for tests and consumers is the ``fetch`` argument to
:func:`resolve_client` (wired through ``OAuthConfig.cimd_fetch``): it replaces
the network fetch only — document validation always runs here, so a fake cannot
accidentally skip the checks the flow depends on.
"""

import hashlib
import http.client
import ipaddress
import json
import socket
import ssl
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

from django.core.cache import cache

#: Matches ``AbstractOAuthClient.client_id`` — a URL that cannot be stored
#: cannot become a client row, so refuse it before fetching anything.
MAX_CLIENT_ID_LENGTH = 500
#: The draft recommends reading no more than ~5KB; real documents are a few
#: hundred bytes. Anything larger is not a metadata document.
MAX_DOCUMENT_BYTES = 16_384
#: More generous than registration's cap of 10: a published document may list a
#: redirect per environment, and it costs nothing to accept.
MAX_DOCUMENT_REDIRECT_URIS = 32
FETCH_TIMEOUT_SECONDS = 5
#: How long a fetched document (or a failure) is believed before re-fetching.
CACHE_TTL_SECONDS = 5 * 60
FAILURE_CACHE_TTL_SECONDS = 60
#: Global, not per-URL: the authorize endpoint is unauthenticated, so distinct
#: attacker-minted URLs would each be a cache miss. The budget bounds the blast
#: radius at "some outbound GETs", full stop.
MAX_FETCHES_PER_MINUTE = 60

_CACHE_PREFIX = "drf_foundation.mcp.cimd:"
_BUDGET_KEY = "drf_foundation.mcp.cimd:budget"


class CimdError(Exception):
    """Refusal, with a message written for the person on the error page."""


@dataclass(frozen=True)
class CimdClient:
    """A validated Client ID Metadata Document, reduced to what the flow uses."""

    client_id: str
    name: str
    origin: str
    redirect_uris: list[str]


def is_cimd_client_id(value: str) -> bool:
    """URL-shaped client ids take the CIMD path; everything else is a row lookup."""
    return value.startswith("https://")


def resolve_client(
    client_id: str, *, fetch: Callable[[str], dict[str, Any]] | None = None
) -> CimdClient:
    """URL checks, fetch, document checks — or :class:`CimdError` saying why not."""
    validate_client_id_url(client_id)
    document = (fetch or fetch_document)(client_id)
    return validate_document(client_id, document)


def validate_client_id_url(url: str) -> None:
    """The draft's shape rules for the identifier itself, before any network I/O."""
    if len(url) > MAX_CLIENT_ID_LENGTH or any(char.isspace() for char in url):
        raise CimdError("The app's identifier URL is malformed.")
    parts = urlsplit(url)
    if (
        parts.scheme != "https"
        or not parts.hostname
        or parts.username is not None
        or parts.password is not None
        or parts.fragment
        or parts.path in ("", "/")
        or any(segment in (".", "..") for segment in parts.path.split("/"))
    ):
        raise CimdError(
            "The app's identifier must be an https URL with a path and no credentials or fragment."
        )


def validate_document(client_id: str, document: Any) -> CimdClient:
    """The draft's rules for the fetched document, against the URL it came from."""
    if not isinstance(document, dict):
        raise CimdError("The app's metadata document is not a JSON object.")
    # Simple string comparison, per the draft — no URL normalization on either side.
    if document.get("client_id") != client_id:
        raise CimdError("The app's metadata document does not claim this client_id.")

    redirect_uris = document.get("redirect_uris")
    # Lazy: oauth.py imports this module at top level, so the reverse import
    # has to wait until call time. Same rules as dynamic registration.
    from drf_foundation.mcp.oauth import _valid_registration_uri

    if (
        not isinstance(redirect_uris, list)
        or not redirect_uris
        or len(redirect_uris) > MAX_DOCUMENT_REDIRECT_URIS
        or not all(isinstance(u, str) and _valid_registration_uri(u) for u in redirect_uris)
    ):
        raise CimdError("The app's metadata document lists no usable redirect URIs.")

    # This server only issues public-client tokens. A document demanding client
    # authentication describes a client this server cannot serve — and one
    # carrying a symmetric secret is broken by design (the draft prohibits it).
    auth_method = document.get("token_endpoint_auth_method", "none")
    if auth_method != "none":
        raise CimdError("Only public clients (token_endpoint_auth_method=none) are supported.")

    # Supersets are fine — Claude Code's real document lists refresh_token too.
    grant_types = document.get("grant_types")
    if grant_types is not None and "authorization_code" not in grant_types:
        raise CimdError("The app does not use the authorization_code grant.")
    response_types = document.get("response_types")
    if response_types is not None and "code" not in response_types:
        raise CimdError("The app does not use the code response type.")

    origin = urlsplit(client_id).hostname or client_id
    name = document.get("client_name")
    if not isinstance(name, str) or not name.strip():
        name = origin
    return CimdClient(
        client_id=client_id,
        name=name.strip()[:100],
        origin=origin,
        redirect_uris=redirect_uris,
    )


# --- the guarded fetch --------------------------------------------------------


def fetch_document(client_id: str) -> dict[str, Any]:
    """The default fetcher: budgeted, cached (failures too), then :func:`_get`."""
    key = _CACHE_PREFIX + hashlib.sha256(client_id.encode()).hexdigest()
    cached = cache.get(key)
    if cached is not None:
        if "error" in cached:
            raise CimdError(cached["error"])
        return cached["document"]
    if _over_budget():
        raise CimdError("Too many app-verification requests right now — retry in a minute.")
    try:
        document = _fetch_fresh(client_id)
    except CimdError as exc:
        cache.set(key, {"error": str(exc)}, FAILURE_CACHE_TTL_SECONDS)
        raise
    cache.set(key, {"document": document}, CACHE_TTL_SECONDS)
    return document


def _over_budget() -> bool:
    try:
        cache.add(_BUDGET_KEY, 0, timeout=60)
        count = cache.incr(_BUDGET_KEY)
    except ValueError:
        # The key expired between add and incr — a fresh window, well under budget.
        return False
    return count > MAX_FETCHES_PER_MINUTE


def _fetch_fresh(url: str) -> dict[str, Any]:
    status, content_type, body = _get(url)
    if status != 200:
        detail = "redirects elsewhere" if 300 <= status < 400 else f"answered {status}"
        raise CimdError(f"The app's metadata document {detail}.")
    if "json" not in (content_type or "").lower():
        raise CimdError("The app's metadata document is not served as JSON.")
    if len(body) > MAX_DOCUMENT_BYTES:
        raise CimdError("The app's metadata document is too large.")
    try:
        document = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise CimdError("The app's metadata document is not valid JSON.") from exc
    if not isinstance(document, dict):
        raise CimdError("The app's metadata document is not a JSON object.")
    return document


def _get(url: str) -> tuple[int, str, bytes]:
    """One pinned HTTPS GET: resolve, vet every address, connect to the vetted one."""
    parts = urlsplit(url)
    host = parts.hostname or ""
    port = parts.port or 443
    address = _vetted_address(host, port)
    try:
        raw = socket.create_connection((address, port), timeout=FETCH_TIMEOUT_SECONDS)
    except OSError as exc:
        raise CimdError("The app's metadata document could not be fetched.") from exc
    try:
        try:
            # SNI and certificate verification use the real hostname; only the
            # TCP connection is pinned to the address vetted above.
            tls = ssl.create_default_context().wrap_socket(raw, server_hostname=host)
        except (ssl.SSLError, OSError) as exc:
            raise CimdError("The app's metadata URL failed TLS verification.") from exc
        connection = http.client.HTTPSConnection(host, port, timeout=FETCH_TIMEOUT_SECONDS)
        connection.sock = tls
        try:
            target = parts.path + (f"?{parts.query}" if parts.query else "")
            connection.request(
                "GET",
                target,
                headers={"Accept": "application/json", "User-Agent": "drf-foundation-cimd"},
            )
            response = connection.getresponse()
            body = response.read(MAX_DOCUMENT_BYTES + 1)
            return response.status, response.getheader("Content-Type", "") or "", body
        except (http.client.HTTPException, OSError) as exc:
            raise CimdError("The app's metadata document could not be fetched.") from exc
        finally:
            connection.close()
    finally:
        raw.close()


def _vetted_address(host: str, port: int) -> str:
    try:
        infos = socket.getaddrinfo(host, port, proto=socket.IPPROTO_TCP)
    except OSError as exc:
        raise CimdError("The app's metadata URL does not resolve.") from exc
    addresses = [info[4][0] for info in infos]
    if not addresses:
        raise CimdError("The app's metadata URL does not resolve.")
    for literal in addresses:
        # A name that resolves to *any* non-public address is refused outright —
        # partial-poisoning (one public, one private answer) must not win a retry.
        if not _is_public_address(literal):
            raise CimdError("The app's metadata URL points somewhere non-public.")
    return addresses[0]


def _is_public_address(literal: str) -> bool:
    try:
        parsed = ipaddress.ip_address(literal.partition("%")[0])
    except ValueError:
        return False
    return parsed.is_global and not (
        parsed.is_multicast
        or parsed.is_reserved
        or parsed.is_loopback
        or parsed.is_link_local
        or parsed.is_private
        or parsed.is_unspecified
    )
