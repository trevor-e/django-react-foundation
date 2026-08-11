"""A representative wire model used to exercise auto-discovery in the test suite."""

from typing import Literal

from drf_foundation.schemas import Pagination, Schema


class Widget(Schema):
    id: int
    name: str
    price: float | None = None


class WidgetList(Schema):
    items: list[Widget]
    pagination: Pagination


class WidgetKind(Schema):
    """Exercises the defaulted-field-required rule: a literal discriminant with a
    default (the discriminated-union idiom) plus a nullable defaulted field."""

    kind: Literal["widget"] = "widget"
    label: str | None = None
    name: str
