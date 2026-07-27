from django.http import HttpResponse
from django.test import RequestFactory, override_settings

from drf_foundation.middleware import ApiVersionAliasMiddleware


def _resolve(path: str) -> dict:
    """Run the middleware and report what URL resolution *would* see."""
    seen = {}

    def get_response(request):
        seen["path_info"] = request.path_info
        seen["path"] = request.path
        return HttpResponse()

    ApiVersionAliasMiddleware(get_response)(RequestFactory().get(path))
    return seen


def test_versioned_path_resolves_as_unversioned():
    assert _resolve("/api/v1/tasks")["path_info"] == "/api/tasks"


def test_unversioned_path_is_untouched():
    assert _resolve("/api/tasks")["path_info"] == "/api/tasks"


def test_request_path_still_shows_what_the_client_sent():
    """Logs and error pages must not lie about the requested URL."""
    seen = _resolve("/api/v1/tasks")
    assert seen["path"] == "/api/v1/tasks"


def test_only_the_prefix_is_replaced():
    """A `v1` appearing later in the path is data, not a version marker."""
    assert _resolve("/api/v1/things/v1/detail")["path_info"] == "/api/things/v1/detail"


def test_non_api_paths_are_untouched():
    assert _resolve("/admin/")["path_info"] == "/admin/"


@override_settings(DRF_FOUNDATION_VERSION_ALIAS=("/api/v2/", "/api/"))
def test_alias_pair_is_configurable():
    assert _resolve("/api/v2/tasks")["path_info"] == "/api/tasks"
    assert _resolve("/api/v1/tasks")["path_info"] == "/api/v1/tasks"
