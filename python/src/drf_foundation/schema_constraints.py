"""Architecture check: every request-schema scalar is bounded.

A Schemathesis fuzz run proved the failure mode (2026-07-25): an unconstrained ``str``
flowing into a bounded ``CharField`` is a ``DataError`` 500 on long input and silent
truncation-or-junk under the limit, and an unconstrained ``int`` overflows a 32-bit
Postgres column the same way. Both are client-triggerable 500s reachable from any request
body.

Fuzzing *finds* those; this makes the class structurally impossible to reintroduce.
Walk the request models reachable from your API surface and fail unless

- every scalar ``str`` field carries a ``max_length`` (or is a ``Literal``), and
- every scalar ``int`` field carries an upper bound (``le``/``lt``).

Response models are exempt: they serialize rows that were bounded on the way in.
Container and id fields are exempt by default — junk ids fail FK resolution with a
4xx, and :func:`drf_foundation.schemas.parse_body` rejects NUL bytes wholesale.

Wire it into a project's suite::

    from drf_foundation.openapi import get_spec
    from drf_foundation.schema_constraints import request_models, unbounded_fields

    ALLOWED = {("ImportPayload", "raw_csv"): "streamed to storage, never a column"}

    def test_every_request_scalar_is_bounded():
        problems = unbounded_fields(request_models(get_spec()), allowed=set(ALLOWED))
        assert not problems, "\\n  ".join(str(p) for p in problems)

Fix a failure by mirroring the model column's constraint in the schema, or allowlist
the field with a reason.
"""

import types
import typing
from dataclasses import dataclass
from typing import TYPE_CHECKING

from annotated_types import Le, Lt, MaxLen
from pydantic import BaseModel

if TYPE_CHECKING:
    from drf_foundation.openapi import OpenApiSpec


@dataclass(frozen=True)
class Unbounded:
    """One unbounded field, with the reason it matters."""

    model: str
    field: str
    problem: str

    def __str__(self) -> str:
        return f"{self.model}.{self.field}: {self.problem}"


def _flatten(annotation: object) -> list[object]:
    """The annotation plus every type argument, recursively (unions, lists, ...)."""
    out = [annotation]
    for arg in typing.get_args(annotation):
        out.extend(_flatten(arg))
    return out


def _scalar_types(annotation: object) -> set[object]:
    """The non-None members of a scalar-or-optional annotation; empty for containers."""
    origin = typing.get_origin(annotation)
    if origin in (typing.Union, types.UnionType):
        return {a for a in typing.get_args(annotation) if a is not type(None)}
    return {annotation}


def _is_literal(annotation: object) -> bool:
    return typing.get_origin(annotation) is typing.Literal


def collect_nested(
    model: type[BaseModel], into: set[type[BaseModel]] | None = None
) -> set[type[BaseModel]]:
    """``model`` plus every nested request model reachable from its fields."""
    models: set[type[BaseModel]] = set() if into is None else into
    if model in models:
        return models
    models.add(model)
    for field in model.model_fields.values():
        for candidate in _flatten(field.annotation):
            if isinstance(candidate, type) and issubclass(candidate, BaseModel):
                collect_nested(candidate, models)
    return models


def request_models(spec: "OpenApiSpec") -> set[type[BaseModel]]:
    """Every request model reachable from an :class:`~drf_foundation.openapi.OpenApiSpec`."""
    models: set[type[BaseModel]] = set()
    for ops in spec.operations.values():
        for op in ops.values():
            if op.request is not None:
                collect_nested(op.request, models)
    return models


def unbounded_fields(
    models: set[type[BaseModel]],
    *,
    allowed: set[tuple[str, str]] | None = None,
) -> list[Unbounded]:
    """Every unbounded scalar across ``models``, sorted for a stable failure message.

    ``allowed`` is a set of ``(model_name, field_name)`` pairs to skip — keep the
    reason next to each entry in the project's own table.
    """
    skip = allowed or set()
    problems: list[Unbounded] = []
    for model in sorted(models, key=lambda m: m.__name__):
        for name, field in model.model_fields.items():
            if (model.__name__, name) in skip:
                continue
            members = _scalar_types(field.annotation)
            if any(_is_literal(m) for m in members):
                continue
            if str in members and not any(isinstance(m, MaxLen) for m in field.metadata):
                problems.append(
                    Unbounded(
                        model.__name__,
                        name,
                        "str without max_length (DataError 500 against a bounded "
                        "column; silent junk otherwise)",
                    )
                )
            if int in members and not any(isinstance(m, Le | Lt) for m in field.metadata):
                problems.append(
                    Unbounded(
                        model.__name__,
                        name,
                        "int without an upper bound (32-bit column overflow is a "
                        "DataError 500 — use le=2147483647)",
                    )
                )
    return problems
