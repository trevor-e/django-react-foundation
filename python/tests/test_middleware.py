import io

from django.http import HttpResponse
from django.test import RequestFactory

from drf_foundation.middleware import ChunkedContentLengthMiddleware


def _run(request):
    captured = {}

    def get_response(req):
        captured["content_length"] = req.META.get("CONTENT_LENGTH")
        return HttpResponse()

    ChunkedContentLengthMiddleware(get_response)(request)
    return captured["content_length"]


def _chunked_request(body: bytes):
    """A request as Django's ASGI handler builds one for a chunked body: the full
    body buffered in `_stream`, and no CONTENT_LENGTH in META."""
    request = RequestFactory().post("/anything")
    request.META.pop("CONTENT_LENGTH", None)
    request._stream = io.BytesIO(body)
    return request


def test_synthesizes_content_length_from_buffered_stream():
    request = _chunked_request(b'{"email": "a@b.c"}')
    assert _run(request) == "18"


def test_stream_is_rewound_for_downstream_readers():
    request = _chunked_request(b"payload")
    _run(request)
    assert request._stream.read() == b"payload"


def test_noop_when_content_length_present():
    request = RequestFactory().post(
        "/anything", data=b"1234", content_type="application/json"
    )
    request._stream = io.BytesIO(b"different")  # must NOT be measured
    assert _run(request) == "4"


def test_noop_for_empty_body():
    request = _chunked_request(b"")
    assert _run(request) is None


def test_noop_without_a_seekable_stream():
    request = RequestFactory().get("/anything")
    request.META.pop("CONTENT_LENGTH", None)
    request._stream = object()  # no seek attr
    assert _run(request) is None
