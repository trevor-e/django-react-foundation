"""Request-correlation consumers on top of django-structlog (``logging`` extra).

django-structlog's ``RequestMiddleware`` binds a per-request ``request_id`` (and
``user_id``) into structlog contextvars. This module adds the two consumers it
doesn't ship:

- :class:`RequestIdHeaderMiddleware` — echo the id as an ``X-Request-ID`` response
  header, so a user/support report can be grepped straight to the log trail. List it
  *below* ``django_structlog.middlewares.RequestMiddleware`` so its response phase
  (bottom-up) runs while the context is still bound.
- :func:`tag_sentry_with_request_id` — a receiver for django-structlog's
  ``bind_extra_request_metadata`` signal that tags Sentry events with the same id.
  Connect it in an ``AppConfig.ready``:

  ```python
  from django_structlog.signals import bind_extra_request_metadata
  from drf_foundation.request_context import tag_sentry_with_request_id

  bind_extra_request_metadata.connect(tag_sentry_with_request_id)
  ```

  ``sentry_sdk.set_tag`` is a no-op when Sentry isn't initialized, so the receiver is
  safe in dev/test.
"""

from collections.abc import Callable

import structlog
from django.http import HttpRequest, HttpResponse


class RequestIdHeaderMiddleware:
    """Echo the bound ``request_id`` contextvar as the ``X-Request-ID`` header."""

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        response = self.get_response(request)
        request_id = structlog.contextvars.get_contextvars().get("request_id")
        if request_id:
            response.headers["X-Request-ID"] = request_id
        return response


def tag_sentry_with_request_id(sender: object, request: HttpRequest, **kwargs: object) -> None:
    """``bind_extra_request_metadata`` receiver: tag the Sentry scope with request_id."""
    try:
        import sentry_sdk
    except ImportError:  # Sentry is optional — projects without it just skip the tag.
        return

    request_id = structlog.contextvars.get_contextvars().get("request_id")
    if request_id:
        sentry_sdk.set_tag("request_id", request_id)
