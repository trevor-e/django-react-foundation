"""Production checks for the MCP surface, as message builders.

Same arrangement as :mod:`drf_foundation.checks`: the package builds messages, the
project registers them and owns the check ids, because id numbering is per-project
and a package cannot pick one without colliding with something.

Deliberately not folded into ``core_production_messages`` — that function is about
core Django configuration every deploy needs, and an app without an MCP server
should not have to reason about a check that cannot apply to it.
"""

from django.core import checks


def issuer_messages(issuer: str, *, check_id: str, hint: str = "") -> list[checks.CheckMessage]:
    """Refuse to boot when the OAuth issuer is not an https origin.

    The discovery documents embed this URL and an MCP client follows whatever they
    say — over http that is an authorization flow (and the bearer token it mints)
    in the clear. Fails closed rather than serving a document that advertises it.

    Callers are responsible for the production gate; this assumes it already passed.
    """
    if issuer.startswith("https://"):
        return []
    return [
        checks.Error(
            "The MCP issuer is not an https origin — OAuth discovery documents and"
            " the MCP resource id would advertise an insecure/localhost URL to"
            " clients.",
            hint=hint or "Point the issuer setting at the deployed https origin.",
            id=check_id,
        )
    ]
