"""Schemathesis config for property-fuzzing the API described by your OpenAPI document.

Promoted from adulting.app. The *harness* (which fixture mints credentials, which path
parameter to pin) is necessarily project-specific; what generalizes — and what took a
real run to work out — is **which Schemathesis checks to leave on, and why the others
lie**. That's what this module encodes.

The schema under test should be the same document ``export_openapi --check`` gates in
CI, loaded through the WSGI app so every generated request runs the real middleware
chain rather than a mock. This is what found the unbounded-field bug class that
:mod:`drf_foundation.schema_constraints` now prevents structurally.

Fuzzing is **opt-in**: a few dozen hypothesis-driven tests are far too slow for the
normal edit-test loop. Mark them and deselect by default::

    # pyproject.toml
    [tool.pytest.ini_options]
    addopts = "-m 'not fuzz'"
    markers = ["fuzz: property-based API fuzzing (slow; run via `make fuzz`)"]

    # tests/test_api_fuzz.py
    import pytest
    from drf_foundation.fuzz import fuzz_config, schema_from_wsgi
    from config.wsgi import application

    pytestmark = pytest.mark.fuzz
    schema = schema_from_wsgi(application, config=fuzz_config())

    @schema.exclude(path_regex="/events/stream").parametrize()
    @settings(deadline=None, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_api_fuzz(case, credentials):
        case.call_and_validate(headers={"Authorization": f"Bearer {credentials.token}"})

Two harness gotchas worth knowing before you write that file:

- Use the ``keep_test_connection`` fixture from this package's pytest plugin. Driving
  the real WSGI handler fires request signals whose ``close_old_connections`` kills the
  test transaction's connection out from under you.
- **Exclude any streaming endpoint.** An SSE response never terminates, so reading its
  body hangs the run — it looks like a fuzzer hang, not a test-design problem.

Requires the ``fuzz`` extra (``schemathesis``).
"""

import os
from typing import Any

#: Each entry is a check Schemathesis enables by default that produces false positives
#: against this stack, with the reason. Kept as data so the rationale travels with the
#: config instead of rotting in a commit message.
DISABLED_CHECKS: dict[str, str] = {
    # Demands 405 for undocumented methods (TRACE etc.). DRF answers 401 because
    # authentication runs before method dispatch — legitimate, and the safer order:
    # an unauthenticated caller shouldn't learn which methods exist.
    "unsupported_method": (
        "DRF authenticates before method dispatch, so undocumented methods 401 rather "
        "than 405 — the safer order, not a bug."
    ),
    # Pydantic coerces some schema-violating values rather than rejecting them (an
    # integer 0 where `format: date` is documented becomes 1970-01-01), so this fires
    # legitimately. Tightening request models to strict validation is a public API
    # contract decision, not a fuzzer fix. Turn it back on once you've made it.
    "negative_data_rejection": (
        "Pydantic coerces some schema-violating input instead of rejecting it; "
        "tightening that is an API contract decision, tracked separately."
    ),
    # Fires on business rules the schema cannot express — opaque cursors, reference
    # ids that don't resolve, require-one-of constraints. Only meaningful with
    # stateful fuzzing that threads real ids between operations.
    "positive_data_acceptance": (
        "Fires on business rules the schema can't express (unresolvable reference "
        "ids, require-one-of); needs stateful fuzzing with real ids to be meaningful."
    ),
}

#: What's deliberately left ON — the checks that carry the actual value.
ENABLED_CHECK_NOTES = (
    "server-error (any unhandled 500) and response-conformance (the response matches "
    "the documented schema) stay enabled — that pair is where the signal is."
)


def fuzz_config_text(
    *, max_examples: int | None = None, disabled_checks: dict[str, str] | None = None
) -> str:
    """The Schemathesis TOML config as text, so a project can inspect or extend it.

    ``with-security-parameters = false``: don't generate Authorization values. Every
    request should carry the harness's real credential — fuzzing the auth layer is a
    different exercise, and generating junk tokens just produces a run of 401s.

    ``no-shrink``: minimal repros are nice, but each shrink step replays real requests,
    and a handful of failing operations turns a run into tens of minutes.
    """
    examples = max_examples or int(os.environ.get("FUZZ_MAX_EXAMPLES", "10"))
    disabled = DISABLED_CHECKS if disabled_checks is None else disabled_checks
    lines = [
        f'generation = {{ mode = "all", max-examples = {examples}, '
        "with-security-parameters = false, no-shrink = true }",
    ]
    for name, reason in disabled.items():
        lines += ["", f"# {reason}", f"[checks.{name}]", "enabled = false"]
    return "\n".join(lines) + "\n"


def fuzz_config(
    *, max_examples: int | None = None, disabled_checks: dict[str, str] | None = None
) -> Any:
    """A ``schemathesis.Config`` with this stack's check decisions applied.

    ``max_examples`` defaults to ``FUZZ_MAX_EXAMPLES`` (else 10) so a deeper hunt is
    an env var rather than an edit: ``FUZZ_MAX_EXAMPLES=100 make fuzz``.
    """
    import schemathesis

    return schemathesis.Config.from_str(
        fuzz_config_text(max_examples=max_examples, disabled_checks=disabled_checks)
    )


def schema_from_wsgi(
    application: Any, *, path: str = "/api/openapi.json", config: Any = None
) -> Any:
    """Load the OpenAPI document *through the WSGI app* rather than off disk.

    That's the point: every generated request then runs the real middleware chain —
    version aliasing, tenancy resolution, auth, the error envelope — so the fuzzer
    exercises the stack a client actually hits.
    """
    import schemathesis

    return schemathesis.openapi.from_wsgi(path, application, config=config or fuzz_config())
