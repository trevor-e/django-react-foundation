import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from drf_foundation.openapi import Op, OpenApiSpec
from tests.testapp.schemas import Widget


@pytest.fixture(autouse=True)
def _widget_urls():
    """Every test in this module builds against the widget URLconf."""
    with override_settings(ROOT_URLCONF="tests.openapi_urls"):
        yield


BASE_KWARGS = dict(
    title="Example API",
    version="1.0.0",
    excluded_url_names=frozenset({"internal-thing"}),
)


def make_spec(**overrides) -> OpenApiSpec:
    kwargs = {
        **BASE_KWARGS,
        "operations": {
            "widget-list": {
                "get": Op(summary="List widgets", response=list[Widget]),
                "post": Op(summary="Create a widget", request=Widget, response=Widget),
            },
            "widget-detail": {"get": Op(summary="Get a widget", response=Widget)},
        },
        **overrides,
    }
    return OpenApiSpec(**kwargs)


def test_documents_every_non_excluded_api_route():
    paths = make_spec().build()["paths"]
    assert set(paths) == {"/api/widgets", "/api/widgets/{widget_id}"}


def test_routes_outside_the_prefix_are_ignored():
    """`not-api/thing` has no registry entry and must not make the build fail."""
    assert "/not-api/thing" not in make_spec().build()["paths"]


def test_undocumented_route_fails_the_build():
    """The exhaustiveness is the point: a new endpoint nobody documented is a build
    failure, not a silent omission."""
    spec = make_spec(excluded_url_names=frozenset())
    with pytest.raises(RuntimeError, match="internal-thing"):
        spec.build()


def test_registry_entry_for_a_vanished_route_fails_the_build():
    """Drift in the other direction — a renamed or deleted route."""
    spec = make_spec(
        operations={
            "widget-list": {"get": Op(summary="List", response=list[Widget])},
            "widget-detail": {"get": Op(summary="Get", response=Widget)},
            "ghost-route": {"get": Op(summary="Gone", response=Widget)},
        }
    )
    with pytest.raises(RuntimeError, match="ghost-route"):
        spec.build()


def test_path_parameters_are_converted():
    item = make_spec().build()["paths"]["/api/widgets/{widget_id}"]
    assert item["parameters"] == [
        {"name": "widget_id", "in": "path", "required": True, "schema": {"type": "string"}}
    ]


def test_path_parameter_descriptions_are_project_supplied():
    spec = make_spec(describe_path_param=lambda converter, name: f"a {converter} called {name}")
    item = spec.build()["paths"]["/api/widgets/{widget_id}"]
    assert item["parameters"][0]["description"] == "a int called widget_id"


def test_list_response_is_an_array_of_refs():
    op = make_spec().build()["paths"]["/api/widgets"]["get"]
    data = op["responses"]["200"]["content"]["application/json"]["schema"]["properties"]["data"]
    assert data == {"type": "array", "items": {"$ref": "#/components/schemas/Widget"}}


def test_request_body_is_referenced():
    op = make_spec().build()["paths"]["/api/widgets"]["post"]
    assert op["requestBody"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/Widget"
    }


def test_204_has_no_content_block():
    spec = make_spec(
        operations={
            "widget-list": {"delete": Op(summary="Delete all", status=204)},
            "widget-detail": {"get": Op(summary="Get", response=Widget)},
        }
    )
    op = spec.build()["paths"]["/api/widgets"]["delete"]
    assert op["responses"]["204"] == {"description": "No content"}


def test_components_are_pruned_to_what_paths_reach():
    """The wire registry is shared with frontend typegen and carries internal-only
    shapes; emitting all of it would publish them as orphaned public components."""
    schemas = make_spec().build()["components"]["schemas"]
    assert "Widget" in schemas
    assert "WidgetList" not in schemas


def test_every_operation_carries_the_error_response():
    op = make_spec().build()["paths"]["/api/widgets"]["get"]
    assert op["responses"]["4XX"] == {"$ref": "#/components/responses/Error"}


def test_unsafe_methods_get_the_note_and_safe_ones_dont():
    spec = make_spec(unsafe_method_note="Requires a read_write key.")
    item = spec.build()["paths"]["/api/widgets"]
    assert item["post"]["description"] == "Requires a read_write key."
    assert "description" not in item["get"]


def test_announce_prefix_rewrites_only_the_announced_path():
    spec = make_spec(announce_prefix=("/api", "/api/v1"))
    assert set(spec.build()["paths"]) == {"/api/v1/widgets", "/api/v1/widgets/{widget_id}"}


def test_tags_default_to_the_module_and_can_be_remapped():
    assert make_spec().build()["paths"]["/api/widgets"]["get"]["tags"] == ["tests"]
    spec = make_spec(tag_by_module={"tests": "Widgets"})
    assert spec.build()["paths"]["/api/widgets"]["get"]["tags"] == ["Widgets"]


def test_security_schemes_are_omitted_when_unset():
    assert "securitySchemes" not in make_spec().build()["components"]


def test_document_is_valid_json_and_declares_openapi_31():
    document = json.loads(make_spec().dump())
    assert document["openapi"] == "3.1.0"
    assert document["info"]["title"] == "Example API"


def test_export_command_check_fails_when_stale(tmp_path):
    output = tmp_path / "openapi.json"
    with override_settings(OPENAPI_SPEC=make_spec()):
        call_command("export_openapi", "--output", str(output))
        assert json.loads(output.read_text())["info"]["title"] == "Example API"
        output.write_text("{}")
        with pytest.raises(CommandError, match="out of date"):
            call_command("export_openapi", "--output", str(output), "--check")


def test_export_command_check_passes_when_fresh(tmp_path):
    output = tmp_path / "openapi.json"
    with override_settings(OPENAPI_SPEC=make_spec()):
        call_command("export_openapi", "--output", str(output))
        call_command("export_openapi", "--output", str(output), "--check")
