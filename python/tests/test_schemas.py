from pydantic import BaseModel
from rest_framework.parsers import JSONParser
from rest_framework.request import Request
from rest_framework.test import APIRequestFactory

from drf_foundation.schemas import (
    RequestValidationError,
    Schema,
    api_exception_handler,
    err,
    ok,
    parse,
)

factory = APIRequestFactory()


def _drf_request(*args, **kwargs) -> Request:
    """``parse()`` expects a DRF ``Request`` (has ``.data``), not the raw WSGIRequest
    ``APIRequestFactory`` builds — wrap it, mirroring what DRF's dispatch does for real.
    ``parsers`` must be supplied explicitly: ``Request.__init__`` only defaults to an
    empty tuple, not ``DEFAULT_PARSER_CLASSES`` (that wiring normally comes from
    ``APIView.initialize_request``, which we're bypassing here)."""
    return Request(factory.post(*args, **kwargs), parsers=[JSONParser()])


class Item(Schema):
    name: str
    count: int = 0


def test_ok_wraps_success_envelope():
    response = ok(Item(name="widget", count=3))
    assert response.status_code == 200
    assert response.data == {"status": "success", "data": {"name": "widget", "count": 3}}


def test_ok_accepts_custom_status():
    response = ok(None, status=201)
    assert response.status_code == 201
    assert response.data == {"status": "success", "data": None}


def test_err_wraps_error_envelope():
    response = err("something broke", status=503)
    assert response.status_code == 503
    assert response.data == {"status": "error", "detail": "something broke"}


def test_parse_valid_body():
    request = _drf_request("/x", {"name": "widget", "count": 5}, format="json")
    parsed = parse(request, Item)
    assert parsed == Item(name="widget", count=5)


def test_parse_missing_required_field_raises():
    request = _drf_request("/x", {"count": 5}, format="json")
    try:
        parse(request, Item)
    except RequestValidationError as exc:
        assert "name" in exc.detail
    else:
        raise AssertionError("expected RequestValidationError")


def test_parse_non_dict_body_falls_back_to_empty_dict():
    class AllOptional(BaseModel):
        count: int = 0

    request = _drf_request("/x", [1, 2, 3], format="json")
    assert parse(request, AllOptional).count == 0


def test_api_exception_handler_renders_validation_error_as_400():
    exc = RequestValidationError("name: field required")
    response = api_exception_handler(exc, {})
    assert response is not None
    assert response.status_code == 400
    assert response.data == {"status": "error", "detail": "name: field required"}


def test_api_exception_handler_delegates_other_exceptions():
    from rest_framework.exceptions import NotFound

    response = api_exception_handler(NotFound(), {"request": None, "view": None})
    assert response is not None
    assert response.status_code == 404


# --- NUL-byte rejection (Postgres text cannot store 0x00; reject at the choke point) ---


class Nested(Schema):
    name: str = ""
    tags: list[str] = []
    child: Item | None = None


def test_parse_rejects_nul_in_top_level_string():
    request = _drf_request("/x", {"name": "bad \x00name"}, format="json")
    try:
        parse(request, Item)
        raise AssertionError("expected RequestValidationError")
    except RequestValidationError as exc:
        assert "NUL" in exc.detail
        assert "name" in exc.detail


def test_parse_rejects_nul_in_nested_list():
    request = _drf_request("/x", {"tags": ["fine", "bad \x00"]}, format="json")
    try:
        parse(request, Nested)
        raise AssertionError("expected RequestValidationError")
    except RequestValidationError as exc:
        assert "tags[1]" in exc.detail


def test_parse_rejects_nul_in_nested_object():
    request = _drf_request("/x", {"child": {"name": "x\x00y"}}, format="json")
    try:
        parse(request, Nested)
        raise AssertionError("expected RequestValidationError")
    except RequestValidationError as exc:
        assert "child.name" in exc.detail


def test_parse_allows_clean_unicode():
    request = _drf_request("/x", {"name": "h\u00e9llo \u00fcn\u00efcode"}, format="json")
    assert parse(request, Nested).name == "h\u00e9llo \u00fcn\u00efcode"
