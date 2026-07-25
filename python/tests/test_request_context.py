"""RequestIdHeaderMiddleware + the Sentry-tag receiver (request_context module)."""

import structlog
from django.http import HttpRequest, HttpResponse

from drf_foundation.request_context import RequestIdHeaderMiddleware, tag_sentry_with_request_id


def test_header_echoed_when_request_id_bound():
    structlog.contextvars.clear_contextvars()
    structlog.contextvars.bind_contextvars(request_id="req-123")
    try:
        middleware = RequestIdHeaderMiddleware(lambda request: HttpResponse())
        response = middleware(HttpRequest())
        assert response.headers["X-Request-ID"] == "req-123"
    finally:
        structlog.contextvars.clear_contextvars()


def test_no_header_without_binding():
    structlog.contextvars.clear_contextvars()
    middleware = RequestIdHeaderMiddleware(lambda request: HttpResponse())
    assert "X-Request-ID" not in middleware(HttpRequest()).headers


def test_sentry_receiver_is_a_noop_without_binding():
    structlog.contextvars.clear_contextvars()
    tag_sentry_with_request_id(sender=None, request=HttpRequest())
