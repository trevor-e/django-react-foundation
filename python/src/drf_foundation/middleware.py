"""Request-plumbing middleware that papers over proxy/protocol mismatches."""

from collections.abc import Callable

from django.http import HttpRequest, HttpResponse


class ChunkedContentLengthMiddleware:
    """Synthesize CONTENT_LENGTH for chunked request bodies under ASGI.

    Reverse proxies (Cloudflare's orange-cloud is the canonical case) re-frame request
    bodies toward the origin as ``Transfer-Encoding: chunked`` with no
    ``Content-Length``. Django's ASGI handler drains such bodies into a spooled temp
    file just fine — but DRF's ``Request._load_stream`` independently treats a
    missing/zero ``CONTENT_LENGTH`` as "no body" and silently parses empty, so every
    proxied POST 400s with "field is required" while the same request works when sent
    directly. This measures the already-buffered body spool (seek to end, seek back —
    no copy, so multi-MB uploads stay off the heap) and fills in the header DRF
    trusts.

    No-op whenever CONTENT_LENGTH is present — i.e. everything not re-framed by a
    proxy, including local dev and the test client. Install first in ``MIDDLEWARE`` so
    every later consumer of the body sees a CONTENT_LENGTH.

    WSGI deployments need more than this (the WSGI spec has no first-class chunked
    bodies at all); this middleware assumes the ASGI handler already buffered the
    stream. Serve via ASGI (see the blueprint's serving section) and this is the only
    piece required.
    """

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if not request.META.get("CONTENT_LENGTH"):
            # ASGIRequest._stream is the fully-buffered body file.
            stream = getattr(request, "_stream", None)
            if stream is not None and hasattr(stream, "seek"):
                try:
                    length = stream.seek(0, 2)
                    stream.seek(0)
                except (OSError, ValueError):
                    length = 0
                if length:
                    request.META["CONTENT_LENGTH"] = str(length)
        return self.get_response(request)


class ApiVersionAliasMiddleware:
    """Serve a versioned URL prefix as an alias of the unversioned one.

    Announcing ``/api/v1`` as the public surface without maintaining a second set of
    urlpatterns: ``/api/v1/<rest>`` resolves as ``/api/<rest>``, so both spellings hit
    the same routes, the same route *names*, the same middleware, the same throttle
    scopes, and the same test coverage. Nothing can drift between them because there
    is only one of them.

    A pre-resolution rewrite is what buys that. Mounting a second ``include()`` under
    the versioned prefix would duplicate every route name and silently double the
    surface any route-walking test (tenancy leak tests, OpenAPI generation) has to
    reason about.

    Only ``path_info`` — the value URL resolution reads — is rewritten. ``request.path``
    is deliberately left alone so logs, error pages, and Sentry events show what the
    client actually sent.

    Configure with ``DRF_FOUNDATION_VERSION_ALIAS`` as a ``(prefix, target)`` pair;
    it defaults to ``("/api/v1/", "/api/")``. Install anywhere in ``MIDDLEWARE`` before
    the view is resolved (i.e. anywhere in the normal list).
    """

    DEFAULT_ALIAS = ("/api/v1/", "/api/")

    def __init__(self, get_response: Callable[[HttpRequest], HttpResponse]) -> None:
        self.get_response = get_response
        from django.conf import settings

        self.prefix, self.target = getattr(
            settings, "DRF_FOUNDATION_VERSION_ALIAS", self.DEFAULT_ALIAS
        )

    def __call__(self, request: HttpRequest) -> HttpResponse:
        if request.path_info.startswith(self.prefix):
            request.path_info = self.target + request.path_info[len(self.prefix) :]
        return self.get_response(request)
