"""A representative wire model used to exercise auto-discovery in the test suite."""

from drf_foundation.schemas import Pagination, Schema


class Widget(Schema):
    id: int
    name: str
    price: float | None = None


class WidgetList(Schema):
    items: list[Widget]
    pagination: Pagination
