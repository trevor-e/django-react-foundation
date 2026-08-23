"""Stateless MCP streamable-HTTP protocol handling.

Hand-rolled on purpose: the Python MCP SDK's streamable-HTTP server owns a
Starlette app with lifespan-managed task groups, and this stack serves ASGI
without the lifespan protocol. The subset a stateless tools-only server needs is
five methods over JSON-RPC, and the wire format is stable across the
handshake-based protocol revisions listed below — so the SDK's shape costs more
than it saves.

"Stateless" means: JSON-RPC over POST answered with single JSON bodies, no
server-assigned session id, no server-push stream. A client initializes, lists
tools, and calls them; nothing is remembered between requests.

The one place a protocol revision has to be edited is
:data:`SUPPORTED_PROTOCOL_VERSIONS` here, which is the point of the module
living in the package rather than in each project.
"""

import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from drf_foundation.mcp.tools import READ_ONLY_REFUSAL, Tool, call as call_tool, definitions

SUPPORTED_PROTOCOL_VERSIONS = ("2025-03-26", "2025-06-18", "2025-11-25")
LATEST_PROTOCOL_VERSION = "2025-11-25"
# Version assumed when the MCP-Protocol-Version header is absent, per the spec.
DEFAULT_PROTOCOL_VERSION = "2025-03-26"

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


@dataclass(frozen=True)
class McpServer:
    """Everything about a server that is the project's to decide.

    ``registry`` maps tool name to :class:`~drf_foundation.mcp.tools.Tool`.
    ``instructions`` is handed the request's context object and returns the text
    an initializing client shows its model — the natural place to name the
    account or tenant the connection is scoped to, and to say when it is
    read-only.

    ``context`` itself is opaque to this package: whatever the view resolved from
    the credential (a tenant plus a key, a user, a request) travels through
    unexamined and reaches the tool handlers as-is.
    """

    name: str
    version: str
    registry: dict[str, Tool]
    instructions: Callable[[Any], str] | None = None
    title: str = ""
    # Whether this request's credential may run write tools. Left unset, every
    # tool runs — say so explicitly rather than defaulting a credential open.
    can_write: Callable[[Any], bool] | None = None
    write_refused: str = READ_ONLY_REFUSAL
    # Reported in `initialize`; a tools-only server has nothing else to declare.
    capabilities: dict[str, Any] = field(
        default_factory=lambda: {"tools": {"listChanged": False}}
    )

    @property
    def info(self) -> dict[str, str]:
        return {"name": self.name, "title": self.title or self.name, "version": self.version}


def _error(msg_id: object, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}


def _result(msg_id: object, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": msg_id, "result": result}


def handle_post(
    server: McpServer,
    context: Any,
    raw_body: bytes,
    header_version: str | None,
) -> tuple[int, dict[str, Any] | None]:
    """Handle one streamable-HTTP POST. Returns ``(http_status, json_body | None)``.

    A ``None`` body means "reply with this status and no content" — the 202 a
    JSON-RPC notification gets. Transport-level failures (an unsupported version
    header) are HTTP errors; everything else is a 200 carrying a JSON-RPC error
    object, which is what the spec asks for and what clients recover from.
    """
    if header_version is not None and header_version not in SUPPORTED_PROTOCOL_VERSIONS:
        return 400, _error(
            None,
            INVALID_REQUEST,
            f"Unsupported MCP-Protocol-Version {header_version!r}; supported: "
            f"{', '.join(SUPPORTED_PROTOCOL_VERSIONS)}.",
        )
    try:
        message = json.loads(raw_body)
    except (json.JSONDecodeError, UnicodeDecodeError):
        return 200, _error(None, PARSE_ERROR, "Body is not valid JSON.")
    if isinstance(message, list):
        return 200, _error(
            None, INVALID_REQUEST, "Batched JSON-RPC messages are not supported."
        )
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0":
        return 200, _error(None, INVALID_REQUEST, "Expected a JSON-RPC 2.0 message.")

    method = message.get("method")
    msg_id = message.get("id")
    params = message.get("params") or {}
    if not isinstance(method, str):
        return 200, _error(msg_id, INVALID_REQUEST, "Missing method.")
    if not isinstance(params, dict):
        return 200, _error(msg_id, INVALID_PARAMS, "params must be an object.")

    if msg_id is None:
        # Notifications (notifications/initialized and friends): accept and ignore.
        return 202, None

    if method == "initialize":
        requested = params.get("protocolVersion")
        negotiated = (
            requested
            if requested in SUPPORTED_PROTOCOL_VERSIONS
            else LATEST_PROTOCOL_VERSION
        )
        result = {
            "protocolVersion": negotiated,
            "capabilities": server.capabilities,
            "serverInfo": server.info,
        }
        if server.instructions is not None:
            result["instructions"] = server.instructions(context)
        return 200, _result(msg_id, result)
    if method == "ping":
        return 200, _result(msg_id, {})
    if method == "tools/list":
        return 200, _result(msg_id, {"tools": definitions(server.registry)})
    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(arguments, dict):
            return 200, _error(msg_id, INVALID_PARAMS, "name and arguments are required.")
        outcome = call_tool(
            server.registry,
            context,
            name,
            arguments,
            can_write=server.can_write,
            write_refused=server.write_refused,
        )
        if outcome is None:
            return 200, _error(msg_id, INVALID_PARAMS, f"Unknown tool: {name}")
        return 200, _result(msg_id, outcome)
    return 200, _error(msg_id, METHOD_NOT_FOUND, f"Method not supported: {method}")
