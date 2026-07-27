import pytest
from django.test import RequestFactory, override_settings
from rest_framework.exceptions import NotAuthenticated, ValidationError

from drf_foundation.errors import (
    enveloped_exception_handler,
    handler404,
    handler500,
)
from drf_foundation.schemas import RequestValidationError


def _get(path: str):
    return RequestFactory().get(path)


def test_api_404_is_json_not_html():
    response = handler404(_get("/api/nope"), Exception())
    assert response.status_code == 404
    assert response["Content-Type"] == "application/json"
    assert response.content == b'{"status": "error", "detail": "Not found."}'


def test_api_500_is_json_not_html():
    response = handler500(_get("/api/boom"))
    assert response.status_code == 500
    assert response["Content-Type"] == "application/json"


@pytest.mark.parametrize("path", ["/admin/", "/", "/apiary/not-the-api"])
def test_non_api_404_keeps_djangos_html_page(path):
    """The prefix test must be a real prefix test — `/apiary` must not match `/api/`,
    or a project with an unlucky route silently loses its HTML error pages."""
    response = handler404(_get(path), Exception())
    assert response.status_code == 404
    assert "text/html" in response["Content-Type"]


@override_settings(DRF_FOUNDATION_API_PREFIX="/rpc/")
def test_prefix_is_configurable():
    assert handler404(_get("/rpc/nope"), Exception())["Content-Type"] == "application/json"
    assert "text/html" in handler404(_get("/api/nope"), Exception())["Content-Type"]


def test_envelope_is_stamped_onto_drfs_bare_detail():
    """DRF's own auth errors are the case the envelope exists for."""
    response = enveloped_exception_handler(NotAuthenticated(), {})
    assert response.data["status"] == "error"
    assert "detail" in response.data


def test_envelope_preserves_field_keyed_validation_detail():
    """`detail` keeps its exact shape — clients reading it are unaffected."""
    response = enveloped_exception_handler(ValidationError({"email": ["Required."]}), {})
    assert response.data["status"] == "error"
    assert response.data["email"] == ["Required."]


def test_already_enveloped_body_is_untouched():
    """A RequestValidationError already routes through `err()`; stamping again would
    reorder keys for no reason."""
    response = enveloped_exception_handler(RequestValidationError("bad"), {})
    assert response.data == {"status": "error", "detail": "bad"}


def test_non_api_exceptions_still_return_none():
    """Anything DRF declines to handle must stay declined, so Django's 500 path runs."""
    assert enveloped_exception_handler(ValueError("not a DRF error"), {}) is None
