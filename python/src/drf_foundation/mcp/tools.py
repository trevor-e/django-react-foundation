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

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ConfigDict, ValidationError

READ_ONLY_REFUSAL = (
    "This connection is read-only, so nothing was changed. Reconnect with write "
    "access (or ask someone who has it to)."
)


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
    gate in :func:`call`; ``idempotent`` is advisory to the client. ``handler``
    receives ``(context, args)`` — the opaque per-request context the view built,
    and the validated argument model — and returns a JSON-serializable payload.
    """

    name: str
    description: str
    args_model: type[ToolArgs]
    handler: Callable[[Any, Any], dict[str, Any]]
    writes: bool = False
    idempotent: bool = False
    destructive: bool = False

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


def tool_result(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, indent=2, ensure_ascii=False)}],
        "isError": is_error,
    }


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
) -> dict[str, Any] | None:
    """Execute a tool. Returns the MCP tool result, or ``None`` for an unknown tool.

    The write gate runs before argument validation deliberately: a read-only
    credential should be told it cannot write, not handed a critique of arguments
    for a call that was never going to happen.
    """
    tool = reg.get(name)
    if tool is None:
        return None
    if tool.writes and can_write is not None and not can_write(context):
        return tool_error(write_refused)
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
