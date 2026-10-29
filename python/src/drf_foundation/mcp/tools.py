"""The tool registry: describing tools to a model, and running one safely.

A tool is a name, a sentence of prose aimed at the model, a pydantic model for
its arguments, and a handler. This module turns that into the JSON an MCP client
expects, validates arguments before the handler ever sees them, and shapes both
results and failures into MCP tool results.

Argument models subclass :class:`ToolArgs`, which forbids unknown fields — a
model that hallucinates an argument gets told so instead of having it silently
dropped. Bound every ``str``/``int`` field (``max_length``, ``le``): these models
are not part of a project's wire-schema pipeline, so whatever bounding that
pipeline enforces elsewhere has to be done by hand here.
"""

import base64
import json
from collections.abc import Callable, Collection
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

READ_ONLY_REFUSAL = (
    "This connection is read-only, so nothing was changed. Reconnect with write "
    "access (or ask someone who has it to)."
)


def missing_permission(permission: str) -> str:
    """The default refusal for a tool whose permission the credential lacks."""
    return (
        f"This connection doesn't have the {permission!r} permission, so nothing was "
        "done. It can be turned on where the connection is managed, or by reconnecting."
    )


@dataclass(frozen=True)
class Image:
    """Image bytes a tool hands the model, sent as an MCP ``image`` content block."""

    data: bytes
    mime_type: str


@dataclass(frozen=True)
class ToolResult:
    """A handler result carrying images as well as the usual JSON payload.

    Return a plain dict when there is nothing but JSON. The payload is serialized
    first, as the text block every result has, then each image as its own block —
    so a client that drops images still gets the ids and names that say what they
    were."""

    payload: dict[str, Any]
    images: tuple[Image, ...] = ()


class ToolError(Exception):
    """A tool-level failure whose message goes back to the model as isError text.

    Raise it for anything the model could plausibly act on — a name that matched
    nothing, a date in the past, a conflicting update. It is not an exception
    channel for bugs: an unexpected error should propagate and be handled as a
    500 by the view, not be dressed up as a tool result the model will believe.
    """


class ToolArgs(BaseModel):
    """Base for tool argument models: unknown fields are an error, not a shrug."""

    model_config = ConfigDict(extra="forbid")


@dataclass(frozen=True)
class Tool:
    """One tool in the catalog.

    ``writes`` drives both the ``readOnlyHint`` annotation and the credential
    gate in :func:`call`; ``idempotent`` is advisory to the client. ``requires``
    names a permission (an opt-in grant beside the access level) the credential
    must hold, gated in :func:`call` the same way. ``handler`` receives
    ``(context, args)`` — the opaque per-request context the view built, and the
    validated argument model — and returns a JSON-serializable payload, or a
    :class:`ToolResult` when it has images to hand back.
    """

    name: str
    description: str
    args_model: type[ToolArgs]
    handler: Callable[[Any, Any], dict[str, Any] | ToolResult]
    writes: bool = False
    idempotent: bool = False
    destructive: bool = False
    requires: str | None = None

    def definition(self) -> dict[str, Any]:
        schema = self.args_model.model_json_schema()
        # The model class name is an implementation detail; the tool's name is
        # the identity a client shows.
        schema.pop("title", None)
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": schema,
            "annotations": {
                "readOnlyHint": not self.writes,
                "destructiveHint": self.destructive,
                "idempotentHint": self.idempotent,
            },
        }


def registry(*tools: Tool) -> dict[str, Tool]:
    """Build a name-keyed registry, rejecting duplicate names outright."""
    built: dict[str, Tool] = {}
    for tool in tools:
        if tool.name in built:
            raise ValueError(f"Duplicate MCP tool name: {tool.name!r}")
        built[tool.name] = tool
    return built


def definitions(reg: dict[str, Tool]) -> list[dict[str, Any]]:
    return [tool.definition() for tool in reg.values()]


def tool_result(payload: dict[str, Any] | ToolResult, *, is_error: bool = False) -> dict[str, Any]:
    result = payload if isinstance(payload, ToolResult) else ToolResult(payload)
    content: list[dict[str, Any]] = [
        {"type": "text", "text": json.dumps(result.payload, indent=2, ensure_ascii=False)}
    ]
    content.extend(
        {
            "type": "image",
            "data": base64.b64encode(image.data).decode("ascii"),
            "mimeType": image.mime_type,
        }
        for image in result.images
    )
    return {"content": content, "isError": is_error}


def tool_error(message: str) -> dict[str, Any]:
    return {"content": [{"type": "text", "text": message}], "isError": True}


def call(
    reg: dict[str, Tool],
    context: Any,
    name: str,
    arguments: dict[str, Any],
    *,
    can_write: Callable[[Any], bool] | None = None,
    write_refused: str = READ_ONLY_REFUSAL,
    permissions: Callable[[Any], Collection[str]] | None = None,
    permission_refused: Callable[[str], str] = missing_permission,
) -> dict[str, Any] | None:
    """Execute a tool. Returns the MCP tool result, or ``None`` for an unknown tool.

    The write and permission gates run before argument validation deliberately: a
    credential that may not run the tool should be told so, not handed a critique
    of arguments for a call that was never going to happen. A tool that
    ``requires`` a permission is refused when ``permissions`` is unset — a
    deployment that never says what a credential holds grants none of them.
    """
    tool = reg.get(name)
    if tool is None:
        return None
    if tool.writes and can_write is not None and not can_write(context):
        return tool_error(write_refused)
    if tool.requires is not None and (
        permissions is None or tool.requires not in permissions(context)
    ):
        return tool_error(permission_refused(tool.requires))
    try:
        args = tool.args_model.model_validate(arguments)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'input'}: {e['msg']}" for e in exc.errors()
        )
        return tool_error(f"Invalid arguments — {problems}")
    try:
        payload = tool.handler(context, args)
    except ToolError as exc:
        return tool_error(str(exc))
    return tool_result(payload)
