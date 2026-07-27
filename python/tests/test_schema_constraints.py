from typing import Annotated, Literal

from annotated_types import Le, MaxLen
from pydantic import BaseModel

from drf_foundation.openapi import Op, OpenApiSpec
from drf_foundation.schema_constraints import (
    collect_nested,
    request_models,
    unbounded_fields,
)


class Bounded(BaseModel):
    title: Annotated[str, MaxLen(200)]
    count: Annotated[int, Le(2147483647)]
    kind: Literal["a", "b"]
    optional_note: Annotated[str, MaxLen(50)] | None = None


class UnboundedStr(BaseModel):
    title: str


class UnboundedInt(BaseModel):
    count: int


class Nested(BaseModel):
    inner: UnboundedStr


def names(problems):
    return {(p.model, p.field) for p in problems}


def test_bounded_model_is_clean():
    assert unbounded_fields({Bounded}) == []


def test_unbounded_str_is_reported():
    assert names(unbounded_fields({UnboundedStr})) == {("UnboundedStr", "title")}


def test_unbounded_int_is_reported():
    assert names(unbounded_fields({UnboundedInt})) == {("UnboundedInt", "count")}


def test_literals_are_exempt():
    """A Literal is bounded by construction — its value set is the constraint."""
    assert not [p for p in unbounded_fields({Bounded}) if p.field == "kind"]


def test_optional_bounded_field_is_clean():
    """`Annotated[str, MaxLen] | None` must not read as unbounded."""
    assert not [p for p in unbounded_fields({Bounded}) if p.field == "optional_note"]


def test_allowlist_suppresses_a_field():
    assert unbounded_fields({UnboundedStr}, allowed={("UnboundedStr", "title")}) == []


def test_nested_models_are_collected():
    assert collect_nested(Nested) == {Nested, UnboundedStr}


def test_nested_unbounded_field_is_found():
    assert ("UnboundedStr", "title") in names(unbounded_fields(collect_nested(Nested)))


def test_self_referential_model_terminates():
    class Node(BaseModel):
        child: "Node | None" = None

    Node.model_rebuild()
    assert collect_nested(Node) == {Node}


def test_request_models_walks_a_spec_and_ignores_responses():
    """Response models are exempt — they serialize rows bounded on the way in."""
    spec = OpenApiSpec(
        title="t",
        version="1",
        operations={
            "r": {
                "post": Op(summary="s", request=Nested, response=UnboundedInt),
                "get": Op(summary="s", response=UnboundedInt),
            }
        },
    )
    assert request_models(spec) == {Nested, UnboundedStr}


def test_failure_message_names_model_field_and_reason():
    (problem,) = unbounded_fields({UnboundedStr})
    rendered = str(problem)
    assert "UnboundedStr.title" in rendered
    assert "max_length" in rendered
