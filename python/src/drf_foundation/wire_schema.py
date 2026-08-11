"""Single source of truth for an API's wire contract, exported as JSON Schema.

Auto-discovers every :class:`~drf_foundation.schemas.Schema` subclass defined in an
``<app>/schemas.py`` module across every app in ``INSTALLED_APPS``, and emits one combined
JSON Schema (all models under ``$defs``). A frontend can generate its TypeScript types from
that file (e.g. with ``json-schema-to-typescript``), so the request/response shapes are
defined once, in Python, and enforced on both ends.

Discovery is automatic and requires no per-app registration: add a ``schemas.py`` to any
installed app and its ``Schema`` subclasses flow into the export on the next run. Output is
sorted/indented deterministically so a stale file is a clean ``git diff``.
"""

import importlib
import inspect
import json
from types import ModuleType
from typing import Any

from django.conf import settings

from drf_foundation.schemas import ApiResponse, Schema

# Excluded from every export: the base class and the generic success envelope (its
# ``data`` is a TypeVar — a frontend unwraps ``data`` and consumes the payload models
# directly, so the envelope itself carries no useful shape to generate).
_EXCLUDED: frozenset[type] = frozenset({Schema, ApiResponse})


def _iter_schema_modules() -> list[ModuleType]:
    """Every ``<app>/schemas.py`` module across ``INSTALLED_APPS`` that actually exists.

    Apps without a ``schemas.py`` are silently skipped — this is discovery, not a
    required convention.
    """
    from django.apps import apps

    modules: list[ModuleType] = []
    for app_config in apps.get_app_configs():
        try:
            modules.append(importlib.import_module(f"{app_config.name}.schemas"))
        except ModuleNotFoundError:
            continue
    return modules


def collect_models() -> list[type[Schema]]:
    """Every wire model discovered across the app ``schemas.py`` modules, sorted by name."""
    found: dict[str, type[Schema]] = {}
    for module in _iter_schema_modules():
        for name, obj in inspect.getmembers(module, inspect.isclass):
            if (
                issubclass(obj, Schema)
                and obj not in _EXCLUDED
                # Only models *defined* here, not merely imported into this module
                # (e.g. a shared `Pagination` re-exported for convenience).
                and obj.__module__ == module.__name__
            ):
                found[name] = obj
    return [found[name] for name in sorted(found)]


# JSON Schema keywords whose *values* are ``name -> subschema`` maps. Their keys are
# names (which may legitimately be "title"), not schema annotations, so we recurse into
# the values but never treat the keys themselves as a strippable annotation.
_NAME_MAP_KEYWORDS = frozenset(
    {"properties", "$defs", "definitions", "patternProperties", "dependentSchemas"}
)


def _strip_titles(node: Any) -> Any:
    """Recursively drop schema-annotation ``title`` keys (only).

    Pydantic stamps a ``title`` annotation on every field, which makes some TS codegen
    tools (e.g. ``json-schema-to-typescript``) hoist each titled property into a noisy
    standalone alias. Interface names come from the ``$defs`` keys, not titles, so
    dropping the annotations yields clean inline property types.

    A field literally *named* ``title`` appears as a key in a ``properties`` map and
    must be preserved — only an annotation ``"title": <string>`` that sits alongside
    other schema keywords is removed.
    """
    if isinstance(node, dict):
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key in _NAME_MAP_KEYWORDS and isinstance(value, dict):
                # Preserve the names (map keys); strip annotations inside each subschema.
                result[key] = {name: _strip_titles(subschema) for name, subschema in value.items()}
            elif key == "title" and isinstance(value, str):
                continue  # a schema annotation — drop it
            else:
                result[key] = _strip_titles(value)
        return result
    if isinstance(node, list):
        return [_strip_titles(item) for item in node]
    return node


def _require_defaulted_fields(node: Any) -> Any:
    """Mark default-valued properties as required, recursively.

    Pydantic's serialization schema leaves fields with defaults out of ``required`` —
    but this foundation's response path (``ok()`` → ``model_dump(mode="json")``)
    always emits them, defaults included. Without this, every literal discriminant
    (``type: Literal["unit_moved"] = "unit_moved"``) and defaulted field generates as
    *optional* TS, and downstream code can't index on discriminants without
    non-null ceremony. Marking them required is the faithful wire contract.
    """
    if isinstance(node, dict):
        # Same name-map-aware traversal as _strip_titles: recurse into the *values* of
        # properties/$defs/... maps without ever handing the map itself to the schema
        # logic below — a field literally named "properties" must not make its
        # enclosing map look like an object schema and grow a phantom "required".
        result: dict[str, Any] = {}
        for key, value in node.items():
            if key in _NAME_MAP_KEYWORDS and isinstance(value, dict):
                result[key] = {
                    name: _require_defaulted_fields(subschema) for name, subschema in value.items()
                }
            else:
                result[key] = _require_defaulted_fields(value)
        properties = result.get("properties")
        if isinstance(properties, dict):
            required = set(result.get("required", []))
            defaulted = {
                name
                for name, prop in properties.items()
                if isinstance(prop, dict) and "default" in prop
            }
            if defaulted - required:
                result["required"] = sorted(required | defaulted)
        return result
    if isinstance(node, list):
        return [_require_defaulted_fields(item) for item in node]
    return node


def build_json_schema() -> dict[str, Any]:
    """Build the combined JSON Schema document for all discovered wire models.

    The document title comes from ``settings.WIRE_SCHEMA_TITLE`` if set, else a generic
    default.
    """
    from pydantic.json_schema import models_json_schema

    title = getattr(settings, "WIRE_SCHEMA_TITLE", "API wire schema")
    models = collect_models()
    _, combined = models_json_schema(
        [(model, "serialization") for model in models],
        ref_template="#/$defs/{model}",
        title=title,
    )
    return _require_defaulted_fields(_strip_titles(combined))


def dump_json_schema() -> str:
    """Serialize the schema deterministically (sorted keys, trailing newline)."""
    return json.dumps(build_json_schema(), indent=2, sort_keys=True) + "\n"
