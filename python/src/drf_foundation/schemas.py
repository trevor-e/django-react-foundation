"""Shared Pydantic wire-schema plumbing for a function-based DRF API.

This is a schema layer that lives *inside* DRF (DRF stays the HTTP layer; Pydantic
governs only the data shapes). It gives three things at once: static types your type
checker reads natively, runtime validation/coercion for request bodies, and JSON Schema
export for sharing the wire contract with a frontend (see :mod:`drf_foundation.wire_schema`).

If the wire format is load-bearing for existing clients, keep ``model_dump(mode="json")``
byte-identical to whatever hand-built dicts it replaces (``datetime``/``Decimal``/``date``
serialize as strings); ``mode="json"`` is required for that and must not be dropped.

Per-app response/request models are expected to live in ``<app>/schemas.py``; the envelope
and the ``ok()``/``err()``/``parse()`` helpers live here.
"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, ValidationError
from rest_framework import status as http_status
from rest_framework.request import Request
from rest_framework.response import Response

# A JSON number that may be int or float. Use it for fields that may be emitted as
# *either* — typically a ``round(...)`` result that falls back to a bare ``0`` (e.g.
# ``round(avg, 2) if avg else 0``). Pydantic's smart union preserves the exact input
# type, so ``0`` stays ``0`` and ``15.0`` stays ``15.0``. Plain ``int``/``float`` are
# still preferred for fields that are always one or the other.
type Number = int | float


class Schema(BaseModel):
    """Base for every wire model.

    ``extra="forbid"`` makes request parsing reject unknown keys (a real 400 instead of
    silently ignoring typos); on response models it is a cheap guard against leaking an
    unintended field. Field *order* in ``model_dump`` follows declaration order, so
    declare fields in the same order any pre-existing JSON used, to keep diffs clean.
    """

    model_config = ConfigDict(extra="forbid")


class ApiResponse[T](Schema):
    """The success envelope: ``{"status": "success", "data": <T>}``.

    ``T`` is the per-endpoint payload — a model, a ``list[...]`` of models, or ``None``.
    """

    status: Literal["success"] = "success"
    data: T


class ApiError(Schema):
    """The error envelope: ``{"status": "error", "detail": <str>}``."""

    status: Literal["error"] = "error"
    detail: str


class Pagination(Schema):
    """A page-metadata block shared by paginated list endpoints."""

    page: int
    page_size: int
    total: int
    total_pages: int


def ok[T](data: T, status: int = http_status.HTTP_200_OK) -> Response:
    """Wrap a typed payload in the success envelope and return a DRF ``Response``.

    ``data`` is whatever the endpoint's payload model is (a ``Schema`` instance, a list
    of them, ``None``, etc.). Nested models serialize recursively via ``mode="json"``.
    """
    return Response(ApiResponse(data=data).model_dump(mode="json"), status=status)


def err(detail: str, status: int = http_status.HTTP_500_INTERNAL_SERVER_ERROR) -> Response:
    """Return the error envelope ``{"status": "error", "detail": ...}`` with ``status``."""
    return Response(ApiError(detail=detail).model_dump(mode="json"), status=status)


def respond(model: BaseModel, status: int = http_status.HTTP_200_OK) -> Response:
    """Dump a model straight to a ``Response`` — for endpoints whose shape is *not* the
    ``ApiResponse``/``ApiError`` envelope (e.g. auth responses a library like SimpleJWT
    already expects). Use :func:`ok` for the standard success envelope."""
    return Response(model.model_dump(mode="json"), status=status)


class RequestValidationError(Exception):
    """Raised by :func:`parse` when a request body fails schema validation.

    Carries a single human-readable ``detail`` string. :func:`api_exception_handler`
    renders it as the ``{"status": "error", "detail": ...}`` envelope with HTTP 400.
    """

    def __init__(self, detail: str) -> None:
        self.detail = detail
        super().__init__(detail)


def _format_validation_error(exc: ValidationError) -> str:
    """Flatten a Pydantic ``ValidationError`` into one readable ``detail`` string."""
    parts: list[str] = []
    for e in exc.errors(include_url=False):
        loc = ".".join(str(p) for p in e["loc"])
        parts.append(f"{loc}: {e['msg']}" if loc else e["msg"])
    return "; ".join(parts)


def parse_body[M: BaseModel](data: object, schema: type[M]) -> M:
    """Validate an already-extracted body object into ``schema``, raising 400 on failure.

    The primitive behind :func:`parse`; call it directly when the body needs shaping
    before validation (e.g. an endpoint that accepts a bare JSON array). On a Pydantic
    ``ValidationError`` it raises :class:`RequestValidationError`, which
    :func:`api_exception_handler` renders as the error envelope (HTTP 400).
    """
    try:
        return schema.model_validate(data)
    except ValidationError as exc:
        raise RequestValidationError(_format_validation_error(exc)) from exc


def parse[M: BaseModel](request: Request, schema: type[M]) -> M:
    """Validate/coerce a request's JSON body into ``schema``, raising 400 on failure.

    A non-object body (empty, list, scalar) is treated as ``{}`` so all-optional models
    fall back to their defaults. Clients get an actionable 400 (the error envelope) for
    any real validation failure instead of a silent default or a 500.
    """
    raw = request.data
    return parse_body(raw if isinstance(raw, dict) else {}, schema)


def api_exception_handler(exc: Exception, context: dict[str, object]) -> Response | None:
    """DRF exception handler that renders :class:`RequestValidationError` as the error
    envelope and delegates everything else to DRF's default handler unchanged.

    Delegating preserves the exact shape of all other error responses (auth 401/403,
    404, throttling, …) so the wire format stays byte-compatible for existing clients.
    Wire this up via ``REST_FRAMEWORK["EXCEPTION_HANDLER"]``.
    """
    if isinstance(exc, RequestValidationError):
        return err(exc.detail, status=http_status.HTTP_400_BAD_REQUEST)

    from rest_framework.views import exception_handler

    return exception_handler(exc, context)
