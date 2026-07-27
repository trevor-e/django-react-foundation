"""JSON error responses for an API surface served alongside Django HTML.

Two gaps this closes, both found by exercising a real API as an outside consumer
(promoted from adulting.app):

- ``Http404`` raised *before* DRF dispatch — a tenancy middleware's cross-tenant
  mismatch, an unregistered path-converter miss, any unmatched route — renders
  Django's HTML 404 page. That's a parse error for a client doing ``response.json()``.
- DRF's own auth/permission errors emit a bare ``{"detail": ...}`` while
  :func:`drf_foundation.schemas.err` emits ``{"status": "error", "detail": ...}``.
  Clients otherwise have to parse two shapes for the same class of failure.

``handler404``/``handler500`` return the envelope for API paths and defer to Django's
stock HTML pages everywhere else, so ``/admin`` keeps its usable error pages. Wire
them in the root URLconf::

    from drf_foundation.errors import handler404, handler500  # noqa: F401

:func:`enveloped_exception_handler` is a *separate* handler from
:func:`drf_foundation.schemas.api_exception_handler` rather than a change to it. That
handler documents byte-compatibility with DRF's default shape as a feature, and
silently stamping a new key onto every error response would break that promise for
projects relying on it. Opt in via ``REST_FRAMEWORK["EXCEPTION_HANDLER"]``::

    REST_FRAMEWORK = {
        "EXCEPTION_HANDLER": "drf_foundation.errors.enveloped_exception_handler",
    }

Both the handlers and the middleware treat "is this an API path?" as a prefix test,
configurable with ``DRF_FOUNDATION_API_PREFIX`` (default ``"/api/"``) for projects
that mount their API elsewhere.
"""

from django.conf import settings
from django.http import HttpRequest, HttpResponse, JsonResponse
from django.views import defaults
from rest_framework.response import Response

DEFAULT_API_PREFIX = "/api/"


def api_prefix() -> str:
    """The path prefix treated as "this is the API, answer in JSON"."""
    return getattr(settings, "DRF_FOUNDATION_API_PREFIX", DEFAULT_API_PREFIX)


def api_error(detail: str, status: int) -> JsonResponse:
    """The error envelope as a plain Django response, for the pre-DRF paths."""
    return JsonResponse({"status": "error", "detail": detail}, status=status)


def handler404(request: HttpRequest, exception: Exception) -> HttpResponse:
    if request.path.startswith(api_prefix()):
        return api_error("Not found.", 404)
    return defaults.page_not_found(request, exception)


def handler500(request: HttpRequest) -> HttpResponse:
    if request.path.startswith(api_prefix()):
        return api_error("Internal server error.", 500)
    return defaults.server_error(request)


def enveloped_exception_handler(exc: Exception, context: dict[str, object]) -> Response | None:
    """:func:`drf_foundation.schemas.api_exception_handler`, plus a ``"status": "error"``
    key on any error body that lacks one.

    Additive by design: ``detail`` keeps its exact shape — including DRF's
    field-keyed validation dicts — so a client reading ``detail`` is unaffected and
    only gains a discriminator. A body that already carries ``status`` (anything
    routed through :func:`drf_foundation.schemas.err`) is passed through untouched
    rather than reordered.
    """
    from drf_foundation.schemas import api_exception_handler

    response = api_exception_handler(exc, context)
    if response is not None and isinstance(response.data, dict) and "status" not in response.data:
        response.data = {"status": "error", **response.data}
    return response
