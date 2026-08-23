"""The JSON-RPC layer: negotiation, dispatch, and the shapes clients recover from.

Driven through :func:`handle_post` rather than a view, because the transport is
the project's to mount and this is the part the package owns.
"""

import json

import pytest
from pydantic import Field

from drf_foundation.mcp import protocol
from drf_foundation.mcp.protocol import (
    LATEST_PROTOCOL_VERSION,
    SUPPORTED_PROTOCOL_VERSIONS,
    McpServer,
    handle_post,
)
from drf_foundation.mcp.tools import Tool, ToolArgs, ToolError, registry


class EchoArgs(ToolArgs):
    text: str = Field(max_length=20)


class CountArgs(ToolArgs):
    pass


def echo(ctx, args: EchoArgs) -> dict:
    return {"said": args.text, "ctx": ctx}


def explode(ctx, args: CountArgs) -> dict:
    raise ToolError("nothing matched that")


def boom(ctx, args: CountArgs) -> dict:
    raise RuntimeError("a real bug")


REGISTRY = registry(
    Tool(name="echo", description="Echo text back.", args_model=EchoArgs, handler=echo),
    Tool(
        name="write_thing",
        description="A write.",
        args_model=CountArgs,
        handler=echo,
        writes=True,
    ),
    Tool(name="explode", description="Fails.", args_model=CountArgs, handler=explode),
    Tool(name="boom", description="Bug.", args_model=CountArgs, handler=boom),
)


def server(**kwargs) -> McpServer:
    defaults = dict(name="test-server", version="1.2.3", registry=REGISTRY)
    return McpServer(**{**defaults, **kwargs})


def rpc(payload: dict, *, srv: McpServer | None = None, ctx="CTX", version=None):
    return handle_post(srv or server(), ctx, json.dumps(payload).encode(), version)


def test_initialize_negotiates_every_supported_version():
    for version in SUPPORTED_PROTOCOL_VERSIONS:
        status, body = rpc(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": version},
            }
        )
        assert status == 200
        assert body["result"]["protocolVersion"] == version


def test_initialize_falls_back_to_latest_for_an_unknown_version():
    # An unknown version in the *body* is negotiated down, not rejected: the client
    # is told what it will get. Only the header is a hard error (below).
    status, body = rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {"protocolVersion": "1999-01-01"},
        }
    )
    assert status == 200
    assert body["result"]["protocolVersion"] == LATEST_PROTOCOL_VERSION


def test_initialize_reports_server_identity_and_instructions():
    srv = server(title="Test Server", instructions=lambda ctx: f"context is {ctx}")
    status, body = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"}, srv=srv)
    result = body["result"]
    assert result["serverInfo"] == {
        "name": "test-server",
        "title": "Test Server",
        "version": "1.2.3",
    }
    assert result["instructions"] == "context is CTX"
    assert result["capabilities"] == {"tools": {"listChanged": False}}


def test_instructions_are_omitted_when_the_server_declares_none():
    status, body = rpc({"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert "instructions" not in body["result"]


def test_an_unsupported_version_header_is_a_transport_error():
    status, body = rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, version="1999-01-01")
    assert status == 400
    assert body["error"]["code"] == protocol.INVALID_REQUEST
    # The error names what is supported, so a client can retry usefully.
    for version in SUPPORTED_PROTOCOL_VERSIONS:
        assert version in body["error"]["message"]


def test_a_supported_version_header_passes():
    for version in SUPPORTED_PROTOCOL_VERSIONS:
        status, body = rpc({"jsonrpc": "2.0", "id": 1, "method": "ping"}, version=version)
        assert (status, body["result"]) == (200, {})


def test_notifications_are_accepted_and_produce_no_body():
    # id-less messages are notifications: acknowledged with 202 and no content.
    status, body = rpc({"jsonrpc": "2.0", "method": "notifications/initialized"})
    assert (status, body) == (202, None)


@pytest.mark.parametrize(
    ("raw", "code"),
    [
        (b"not json", protocol.PARSE_ERROR),
        (b"[]", protocol.INVALID_REQUEST),
        (b'{"jsonrpc": "1.0", "id": 1}', protocol.INVALID_REQUEST),
        (b'{"jsonrpc": "2.0", "id": 1}', protocol.INVALID_REQUEST),
        (b'{"jsonrpc": "2.0", "id": 1, "method": "x", "params": 4}', protocol.INVALID_PARAMS),
    ],
)
def test_malformed_messages_answer_200_with_a_jsonrpc_error(raw, code):
    # Malformed *content* is a JSON-RPC error at HTTP 200 — that is the shape the
    # spec asks for, and the one clients surface instead of dying.
    status, body = handle_post(server(), "CTX", raw, None)
    assert status == 200
    assert body["error"]["code"] == code


def test_batched_messages_are_refused_in_words():
    status, body = handle_post(server(), "CTX", b"[]", None)
    assert "Batched" in body["error"]["message"]


def test_unknown_method_is_method_not_found():
    status, body = rpc({"jsonrpc": "2.0", "id": 1, "method": "resources/list"})
    assert body["error"]["code"] == protocol.METHOD_NOT_FOUND


def test_tools_list_returns_definitions_with_annotations():
    status, body = rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/list"})
    tools = {t["name"]: t for t in body["result"]["tools"]}
    assert set(tools) == {"echo", "write_thing", "explode", "boom"}
    assert tools["echo"]["annotations"]["readOnlyHint"] is True
    assert tools["write_thing"]["annotations"]["readOnlyHint"] is False
    assert "text" in tools["echo"]["inputSchema"]["properties"]
    # The args model's class name is an implementation detail, not the tool's identity.
    assert "title" not in tools["echo"]["inputSchema"]


def test_tools_call_runs_the_handler_with_the_context():
    status, body = rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "hi"}},
        }
    )
    result = body["result"]
    assert result["isError"] is False
    assert json.loads(result["content"][0]["text"]) == {"said": "hi", "ctx": "CTX"}


def test_unknown_tool_is_invalid_params_not_a_tool_error():
    status, body = rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "nope"}}
    )
    assert body["error"]["code"] == protocol.INVALID_PARAMS
    assert "nope" in body["error"]["message"]


def test_bad_arguments_come_back_as_a_readable_tool_error():
    status, body = rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "x" * 50}},
        }
    )
    result = body["result"]
    assert result["isError"] is True
    assert "text" in result["content"][0]["text"]


def test_unknown_arguments_are_refused_rather_than_dropped():
    # extra="forbid": a hallucinated argument gets named, not silently ignored.
    status, body = rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "hi", "colour": "red"}},
        }
    )
    assert body["result"]["isError"] is True
    assert "colour" in body["result"]["content"][0]["text"]


def test_a_tool_error_is_a_result_but_a_bug_propagates():
    status, body = rpc(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "explode"}}
    )
    assert body["result"]["isError"] is True
    assert body["result"]["content"][0]["text"] == "nothing matched that"

    # An unexpected exception must NOT be dressed up as a tool result the model
    # will believe — it propagates for the view to turn into a 500.
    with pytest.raises(RuntimeError):
        rpc({"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {"name": "boom"}})


def test_the_write_gate_refuses_before_validating_arguments():
    srv = server(can_write=lambda ctx: False, write_refused="read-only here")
    status, body = rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "write_thing", "arguments": {"bogus": 1}},
        },
        srv=srv,
    )
    # Told it cannot write, rather than handed a critique of arguments for a call
    # that was never going to happen.
    assert body["result"]["content"][0]["text"] == "read-only here"


def test_read_tools_still_run_for_a_read_only_credential():
    srv = server(can_write=lambda ctx: False)
    status, body = rpc(
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "echo", "arguments": {"text": "hi"}},
        },
        srv=srv,
    )
    assert body["result"]["isError"] is False


def test_duplicate_tool_names_are_rejected_at_registry_build():
    tool = Tool(name="echo", description="d", args_model=EchoArgs, handler=echo)
    with pytest.raises(ValueError, match="Duplicate MCP tool name"):
        registry(tool, tool)
