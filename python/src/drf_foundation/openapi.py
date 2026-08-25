"""OpenAPI 3.1 document generation from the wire-schema registry + the live URLconf.

The package already exports the wire models as JSON Schema
for frontend typegen (:mod:`drf_foundation.wire_schema`); this is the same source of
truth aimed at a second consumer — a public API document.

The document is assembled from two things that already exist, so neither can go stale:

- **Shapes** come from the Pydantic wire models — the same registry that generates the
  frontend's types — emitted under ``#/components/schemas``.
- **Paths** come from walking the live URLconf, so a route that isn't registered can't
  be documented, and a documented route that disappears fails the build.

What the URLconf *can't* tell us — which wire model each operation consumes and returns
— lives in an :class:`OpenApiSpec`'s ``operations`` registry, keyed by URL name. The
build fails loudly on drift in either direction: an API route with no registry entry,
or a registry entry naming a route that no longer exists. Wire ``--check`` into CI
(see the ``export_openapi`` command) and neither can rot silently.

drf-spectacular is deliberately not used: it documents DRF serializers, and in this
stack the wire types live in Pydantic — it would document the wrong layer.

Usage::

    # config/openapi.py
    from drf_foundation.openapi import Op, OpenApiSpec

    SPEC = OpenApiSpec(
        title="Example API",
        version="1.0.0",
        servers=[{"url": "https://api.example.com"}],
        excluded_url_names=frozenset({"health-check", "auth-login"}),
        operations={
            "task-list": {
                "get": Op(summary="List tasks", response=list[TaskSchema]),
                "post": Op(summary="Create a task", request=CreateTask, response=TaskSchema),
            },
        },
    )

    # settings.py
    OPENAPI_SPEC = "config.openapi.SPEC"
"""

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from django.urls import get_resolver
from django.urls.resolvers import URLPattern, URLResolver

SAFE_METHODS = frozenset({"get", "head", "options"})


@dataclass(frozen=True)
class Op:
    """One documented operation.

    ``response=SomeSchema`` or ``response=list[SomeSchema]``; ``response=None`` with
    ``status=204`` means an empty response.
    """

    summary: str
    request: type | None = None
    response: object = None
    status: int = 200
    media_type: str = "application/json"
    description: str = ""
    query: tuple[tuple[str, str], ...] = field(default_factory=tuple)


def _walk(patterns: list, prefix: str = "") -> list[tuple[str, URLPattern]]:
    routes: list[tuple[str, URLPattern]] = []
    for entry in patterns:
        if isinstance(entry, URLResolver):
            routes.extend(_walk(entry.url_patterns, prefix + str(entry.pattern)))
        else:
            routes.append((prefix + str(entry.pattern), entry))
    return routes


def _refs_in(node: object) -> set[str]:
    """Every ``#/components/schemas/<Name>`` referenced anywhere under ``node``."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "$ref" and isinstance(value, str):
                found.add(value.rsplit("/", 1)[-1])
            else:
                found |= _refs_in(value)
    elif isinstance(node, list):
        for item in node:
            found |= _refs_in(item)
    return found


def _ref(model: type) -> dict[str, Any]:
    return {"$ref": f"#/components/schemas/{model.__name__}"}


@dataclass
class OpenApiSpec:
    """Everything the URLconf and the wire registry can't supply on their own."""

    title: str
    version: str
    operations: dict[str, dict[str, Op]]
    description: str = ""
    servers: list[dict[str, str]] = field(default_factory=list)
    #: Routes that exist but are deliberately not public — sign-in flows, webhooks with
    #: their own signature contract, staff-only surfaces, infra. Every route under
    #: ``route_prefix`` must be either documented or listed here; that exhaustiveness
    #: is the point, since it makes "I forgot to document the new endpoint" a build
    #: failure rather than a silent omission.
    excluded_url_names: frozenset[str] = frozenset()
    #: Which routes to consider at all.
    route_prefix: str = "api/"
    #: Announce paths under a different prefix than they're served at — pair with
    #: :class:`drf_foundation.middleware.ApiVersionAliasMiddleware` so both spellings
    #: resolve identically. ``("/api", "/api/v1")`` rewrites the announced path only.
    announce_prefix: tuple[str, str] | None = None
    #: Top-level module name -> tag. Unmapped modules tag as themselves.
    tag_by_module: dict[str, str] = field(default_factory=dict)
    security_schemes: dict[str, Any] = field(default_factory=dict)
    #: Applied to every operation unless overridden.
    security: list[dict[str, list[str]]] = field(default_factory=list)
    #: ``(converter, name) -> description`` for path parameters, so a project can
    #: explain its custom converters (e.g. a TypeID prefix) without this module
    #: knowing anything about them.
    describe_path_param: Callable[[str, str], str] = lambda converter, name: ""
    #: Appended to the description of every unsafe-method operation, e.g. a note about
    #: which credential scope is required to write.
    unsafe_method_note: str = ""
    openapi_version: str = "3.1.0"

    # -- route discovery ---------------------------------------------------------

    def api_routes(self) -> list[tuple[str, URLPattern]]:
        """Every route under ``route_prefix`` that belongs in the document."""
        return [
            (path, entry)
            for path, entry in _walk(get_resolver().url_patterns)
            if path.startswith(self.route_prefix) and entry.name not in self.excluded_url_names
        ]

    def _openapi_path(self, django_path: str) -> tuple[str, list[dict[str, Any]]]:
        """``api/h/<uuid:household_id>`` -> ``/api/h/{household_id}`` plus its
        OpenAPI parameter objects."""
        parameters: list[dict[str, Any]] = []
        out: list[str] = []
        for segment in django_path.split("/"):
            if not (segment.startswith("<") and segment.endswith(">")):
                out.append(segment)
                continue
            converter, _, name = segment[1:-1].partition(":")
            if not name:
                converter, name = "str", converter
            description = self.describe_path_param(converter, name)
            parameters.append(
                {
                    "name": name,
                    "in": "path",
                    "required": True,
                    "schema": {"type": "string"},
                    **({"description": description} if description else {}),
                }
            )
            out.append("{" + name + "}")
        return "/" + "/".join(out), parameters

    # -- operation rendering -----------------------------------------------------

    def _data_schema(self, response: object) -> dict[str, Any]:
        if response is None:
            return {"type": "null"}
        if response is str:
            return {"type": "string"}
        origin = getattr(response, "__origin__", None)
        if origin is list:
            (item,) = response.__args__  # type: ignore[union-attr]
            return {"type": "array", "items": _ref(item)}
        if not isinstance(response, type):
            raise TypeError(f"unsupported response declaration {response!r}")
        return _ref(response)

    def _success_body(self, op: Op) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["status", "data"],
            "properties": {
                "status": {"const": "success"},
                "data": self._data_schema(op.response),
            },
        }

    def _operation(self, op: Op, url_name: str, method: str, tag: str) -> dict[str, Any]:
        description_parts = [op.description] if op.description else []
        if method not in SAFE_METHODS and self.unsafe_method_note:
            description_parts.append(self.unsafe_method_note)

        operation: dict[str, Any] = {
            "operationId": f"{url_name.replace('-', '_')}_{method}",
            "summary": op.summary,
            "tags": [tag],
            "responses": {
                str(op.status): (
                    {"description": "No content"}
                    if op.status == 204
                    else {
                        "description": "Success",
                        "content": {op.media_type: {"schema": self._success_body(op)}},
                    }
                ),
                "4XX": {"$ref": "#/components/responses/Error"},
            },
        }
        if self.security:
            operation["security"] = self.security
        if description_parts:
            operation["description"] = "\n\n".join(description_parts)
        if op.request is not None:
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": _ref(op.request)}},
            }
        if op.query:
            operation["parameters"] = [
                {
                    "name": name,
                    "in": "query",
                    "required": False,
                    "schema": {"type": "string"},
                    "description": desc,
                }
                for name, desc in op.query
            ]
        return operation

    # -- components --------------------------------------------------------------

    def _components_schemas(self, paths: dict[str, Any]) -> dict[str, Any]:
        """Only models transitively ``$ref``-reachable from the documented paths.

        The wire-model registry is shared with the internal frontend typegen, so it
        also carries internal-only shapes (staff dashboards, ops surfaces) whose routes
        are excluded here. Emitting the whole registry would publish those as orphaned
        public components, so prune to what the public paths actually reach.
        """
        from pydantic.json_schema import models_json_schema

        from drf_foundation.wire_schema import _strip_titles, collect_models

        _, combined = models_json_schema(
            [(model, "serialization") for model in collect_models()],
            ref_template="#/components/schemas/{model}",
        )
        defs = _strip_titles(combined)["$defs"]

        reachable = _refs_in(paths)
        frontier = set(reachable)
        while frontier:
            frontier = {
                name for parent in frontier for name in _refs_in(defs.get(parent, {}))
            } - reachable
            reachable |= frontier
        return {name: schema for name, schema in defs.items() if name in reachable}

    def _error_response(self) -> dict[str, Any]:
        return {
            "type": "object",
            "required": ["status", "detail"],
            "properties": {"status": {"const": "error"}, "detail": {"type": "string"}},
        }

    # -- build -------------------------------------------------------------------

    def build(self) -> dict[str, Any]:
        paths: dict[str, dict[str, Any]] = {}
        documented: set[str] = set()

        for django_path, entry in self.api_routes():
            url_name = entry.name
            if url_name is None:
                raise RuntimeError(f"unnamed API route: {django_path}")
            ops = self.operations.get(url_name)
            if ops is None:
                raise RuntimeError(
                    f"API route {django_path!r} (name={url_name!r}) has no operations "
                    "entry — document it, or add it to excluded_url_names."
                )
            documented.add(url_name)

            path, path_params = self._openapi_path(django_path)
            view_class = getattr(entry.callback, "cls", None)
            module = (view_class or entry.callback).__module__.split(".")[0]
            tag = self.tag_by_module.get(module, module)
            item: dict[str, Any] = {
                method: self._operation(op, url_name, method, tag) for method, op in ops.items()
            }
            if path_params:
                item["parameters"] = path_params
            paths[path] = item

        stale = set(self.operations) - documented
        if stale:
            raise RuntimeError(
                f"operations entries with no matching route (renamed or removed?): {sorted(stale)}"
            )

        if self.announce_prefix is not None:
            served, announced = self.announce_prefix
            paths = {announced + p.removeprefix(served): op for p, op in paths.items()}

        components: dict[str, Any] = {
            "schemas": dict(sorted(self._components_schemas(paths).items())),
            "responses": {
                "Error": {
                    "description": "Client error",
                    "content": {"application/json": {"schema": self._error_response()}},
                }
            },
        }
        if self.security_schemes:
            components["securitySchemes"] = self.security_schemes

        document: dict[str, Any] = {
            "openapi": self.openapi_version,
            "info": {"title": self.title, "version": self.version},
            "paths": dict(sorted(paths.items())),
            "components": components,
        }
        if self.description:
            document["info"]["description"] = self.description
        if self.servers:
            # A concrete origin, not "/": SDK generators and agents pointed at a
            # committed spec file have no request context to resolve a relative URL.
            document["servers"] = self.servers
        return document

    def dump(self) -> str:
        import json

        return json.dumps(self.build(), indent=2, sort_keys=False) + "\n"


def get_spec() -> OpenApiSpec:
    """The project's spec from ``settings.OPENAPI_SPEC`` (a dotted path or an
    :class:`OpenApiSpec`)."""
    from django.conf import settings
    from django.utils.module_loading import import_string

    configured = getattr(settings, "OPENAPI_SPEC", None)
    if configured is None:
        raise RuntimeError("settings.OPENAPI_SPEC is not set — point it at an OpenApiSpec.")
    if isinstance(configured, OpenApiSpec):
        return configured
    return import_string(configured)
