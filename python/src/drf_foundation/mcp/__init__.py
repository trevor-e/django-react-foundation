"""Remote MCP server plumbing: the streamable-HTTP protocol, a tool registry, an
API-key credential store, and the OAuth 2.1 subset MCP clients need to connect.

What this package is for: an app that wants "connect your data to Claude (or any
MCP client)" needs a JSON-RPC endpoint, a tool catalog, a credential to
authenticate it, and — because claude.ai's custom-connector UI offers a URL and
nothing else — an OAuth authorization server. Only the tool catalog is
app-specific. Everything else is protocol, and it lives here.

What stays in the project: the tools themselves, and one
:class:`~drf_foundation.mcp.oauth.OAuthProvider` supplying identity and minting.
Nothing about PKCE, redirect matching, code single-use, or token hashing is
overridable — a provider chooses *who* may connect to *what*, never how the
handshake runs.

Tenancy is the project's, not this package's: a "resource" is whatever a token
acts on. A multi-tenant app returns one resource per tenant the user belongs to
and the consent page renders a picker; a single-tenant app returns the user
themselves and it renders none.

Ships no migrations, in keeping with the rest of the package — the OAuth tables
are abstract bases (:mod:`drf_foundation.mcp.models`) that projects subclass with
their own resource FK, exactly as :class:`drf_foundation.event_log.EventLogEntry`
is used.
"""

from drf_foundation.mcp.api_keys import (
    AbstractApiKey,
    TokenCodec,
    bearer_token,
    resolve_token,
)
from drf_foundation.mcp.models import (
    AbstractAuthorizationCode,
    AbstractGrant,
    AbstractOAuthClient,
)
from drf_foundation.mcp.oauth import (
    McpOAuth,
    MintRefused,
    OAuthConfig,
    OAuthModels,
    OAuthProvider,
    Resource,
    Scope,
    login_redirect,
    permissive_cors,
    redirect_uri_allowed,
)
from drf_foundation.mcp.protocol import (
    LATEST_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    McpServer,
    handle_post,
)
from drf_foundation.mcp.tools import Tool, ToolArgs, ToolError, registry

__all__ = [
    "LATEST_PROTOCOL_VERSION",
    "SUPPORTED_PROTOCOL_VERSIONS",
    "AbstractApiKey",
    "AbstractAuthorizationCode",
    "AbstractGrant",
    "AbstractOAuthClient",
    "McpOAuth",
    "McpServer",
    "MintRefused",
    "OAuthConfig",
    "OAuthModels",
    "OAuthProvider",
    "Resource",
    "Scope",
    "Tool",
    "ToolArgs",
    "ToolError",
    "TokenCodec",
    "bearer_token",
    "handle_post",
    "login_redirect",
    "permissive_cors",
    "redirect_uri_allowed",
    "registry",
    "resolve_token",
]
