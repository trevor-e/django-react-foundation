from drf_foundation.wire_schema import build_json_schema, collect_models, dump_json_schema


def test_collects_wire_models_across_apps():
    names = {m.__name__ for m in collect_models()}
    assert {"Widget", "WidgetList"} <= names
    # The base class and the generic envelope are excluded from the export.
    assert "Schema" not in names
    assert "ApiResponse" not in names


def test_collect_models_does_not_double_count_re_exported_models():
    # WidgetList's schemas module imports `Pagination` from drf_foundation.schemas for
    # convenience — since drf_foundation is itself an installed app, `Pagination` is
    # discovered where it's actually defined; importing it into testapp.schemas too
    # must not produce a second entry.
    names = [m.__name__ for m in collect_models()]
    assert names.count("Pagination") == 1


def test_schema_has_defs_and_no_title_annotations():
    schema = build_json_schema()
    defs = schema["$defs"]
    assert "Widget" in defs
    assert "title" not in defs["Widget"]
    assert all("title" not in prop for prop in defs["Widget"]["properties"].values())


def test_dump_json_schema_is_deterministic():
    first = dump_json_schema()
    second = dump_json_schema()
    assert first == second
    assert first.endswith("\n")


def test_defaulted_fields_are_required_in_serialization():
    """The response path always emits defaults (`ok()` dumps without exclude_*), so
    the wire contract marks them required — literal discriminants especially, which
    would otherwise generate as optional TS and break discriminated narrowing."""
    from drf_foundation.wire_schema import build_json_schema

    schema = build_json_schema()
    widget = schema["$defs"]["WidgetKind"]
    assert "kind" in widget["required"]
    assert widget["properties"]["kind"]["default"] == "widget"
