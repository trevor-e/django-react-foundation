---
feature: Pydantic wire models, deterministic schema export, OpenAPI, and frontend type generation
code: [python/src/drf_foundation/schemas.py, python/src/drf_foundation/wire_schema.py, python/src/drf_foundation/openapi.py, python/src/drf_foundation/schema_constraints.py, python/src/drf_foundation/management/commands/export_*.py, bin/gen-types.js]
---
# wire-contracts

## Rules
- [wire-contracts.single-source] Pydantic `Schema` models in installed apps' `schemas.py` modules are the single source of truth for runtime validation, JSON Schema, OpenAPI shapes, and generated frontend types. {pre-kanspec}
- [wire-contracts.strict-input] Wire models forbid unknown fields, request parsing rejects NUL bytes anywhere in JSON-like input, and validation failures become the standard HTTP 400 error envelope. {pre-kanspec}
- [wire-contracts.serialization] Responses serialize with Pydantic JSON mode so dates, datetimes, decimals, and other wire values retain JSON-compatible representations. {pre-kanspec}
- [wire-contracts.deterministic] Schema and OpenAPI exports are deterministic, and their `--check` commands fail rather than rewrite when committed artifacts have drifted. {pre-kanspec}
- [wire-contracts.exhaustive-openapi] Every live route under an OpenAPI spec's API prefix is either registered as an operation or explicitly excluded; stale registrations and undocumented routes are build errors. {pre-kanspec}
- [wire-contracts.generated-types] Frontend API types are generated from the exported schema with a generated-file warning and unreachable definitions included. {pre-kanspec}
