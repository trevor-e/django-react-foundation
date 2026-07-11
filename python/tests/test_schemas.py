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
