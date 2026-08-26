"""Two fixture providers — one per tenancy shape — and the servers wired to them.

The point of having both is that the handshake code has no branch for them: the
same views, the same template, the same assertions. A multi-tenant app grants
against a row a user belongs to; a single-tenant app grants against the user. If
the second ever needs its own code path in the package, something has gone wrong.
"""

from typing import Any

from django.utils import timezone
from pydantic import Field

from drf_foundation.mcp import (
    McpOAuth,
    McpServer,
    MintRefused,
    OAuthConfig,
    OAuthModels,
    Resource,
    Scope,
    TokenCodec,
    Tool,
    ToolArgs,
    login_redirect,
    mcp_endpoint,
    registry,
)
from tests.testapp.models import (
    Account,
    ApiKey,
    AuthorizationCode,
    Grant,
    OAuthClient,
    UserAuthorizationCode,
    UserGrant,
)

CODEC = TokenCodec(prefix="tk_")
KEY_QUOTA = 3

SCOPES = (
    Scope(value="read", label="Read-only", description="see everything, change nothing"),
    Scope(value="read_write", label="Read & write", description="see and change things"),
)


class _MintMixin:
    scopes = SCOPES

    def mint(self, *, user: Any, resource: Resource, scope: str, client: Any) -> tuple[str, ApiKey]:
        account = self._account_for(user, resource)
        active = ApiKey.objects.filter(account=account, revoked_at__isnull=True).count()
        if active >= KEY_QUOTA:
            raise MintRefused(f"Already at {KEY_QUOTA} active keys — revoke one and retry.")
        secret = CODEC.generate()
        key = ApiKey.objects.create(
            account=account,
            name=client.name[:100],
            key_hash=CODEC.hash(secret),
            key_prefix=CODEC.display_prefix(secret),
            scope=scope,
        )
        return secret, key

    def revoke(self, key: ApiKey, *, reason: str) -> None:
        self.revoked.append((key.pk, reason))
        key.revoked_at = timezone.now()
        key.save(update_fields=["revoked_at"])


class MultiTenantProvider(_MintMixin):
    """Grants against an Account the user is a member of — the household shape."""

    def __init__(self) -> None:
        self.revoked: list[tuple[Any, str]] = []

    def _account_for(self, user: Any, resource: Resource) -> Account:
        return resource.obj

    def memberships(self, user: Any) -> list[Account]:
        return list(Account.objects.filter(members=user).order_by("name"))

    def resources(self, user: Any) -> list[Resource]:
        return [Resource(id=str(a.pk), label=a.name, obj=a) for a in self.memberships(user)]

    def resolve(self, user: Any, resource_id: str) -> Resource | None:
        if not resource_id.isdigit():
            return None
        account = Account.objects.filter(pk=int(resource_id), members=user).first()
        if account is None:
            return None
        return Resource(id=str(account.pk), label=account.name, obj=account)

    def replace_previous(self, *, user: Any, resource: Resource, client: Any) -> None:
        for key in ApiKey.objects.filter(
            grant__client=client, account=resource.obj, revoked_at__isnull=True
        ):
            self.revoke(key, reason="reconnected")


class SingleTenantProvider(_MintMixin):
    """Grants against the user themselves — the per-user shape. One resource, so
    the consent page renders no picker."""

    def __init__(self) -> None:
        self.revoked: list[tuple[Any, str]] = []

    def _account_for(self, user: Any, resource: Resource) -> Account:
        # This fixture still parks keys on an Account row so both shapes can share
        # one ApiKey table; a real per-user app would hang them off the user.
        account, _ = Account.objects.get_or_create(name=f"personal:{user.pk}")
        return account

    def resources(self, user: Any) -> list[Resource]:
        return [Resource(id=str(user.pk), label=user.email or str(user.pk), obj=user)]

    def resolve(self, user: Any, resource_id: str) -> Resource | None:
        if str(user.pk) != resource_id:
            return None
        return Resource(id=str(user.pk), label=user.email or str(user.pk), obj=user)

    def replace_previous(self, *, user: Any, resource: Resource, client: Any) -> None:
        for key in ApiKey.objects.filter(
            user_grant__client=client, user_grant__owner=user, revoked_at__isnull=True
        ):
            self.revoke(key, reason="reconnected")


# --- CIMD fake fetch ----------------------------------------------------------
#
# The seam replaces only the network fetch: tests seed raw documents here, and
# the package still runs its own validation over whatever comes back. The real
# guarded fetcher is unit-tested separately in test_mcp_cimd.py.

CIMD_DOCUMENTS: dict[str, Any] = {}
CIMD_FETCHES: list[str] = []


def fake_cimd_fetch(url: str) -> dict[str, Any]:
    from drf_foundation.mcp.cimd import CimdError

    CIMD_FETCHES.append(url)
    try:
        return CIMD_DOCUMENTS[url]
    except KeyError:
        raise CimdError("The app's metadata document could not be fetched.") from None


def _config(**overrides: Any) -> OAuthConfig:
    defaults: dict[str, Any] = {
        "issuer": lambda: "https://api.example.test",
        "resource_name": "Example",
        "codec": CODEC,
        "login_url": login_redirect("https://app.example.test"),
        "cimd_fetch": fake_cimd_fetch,
    }
    return OAuthConfig(**{**defaults, **overrides})


MULTI_PROVIDER = MultiTenantProvider()
SINGLE_PROVIDER = SingleTenantProvider()

MULTI_OAUTH = McpOAuth(
    provider=MULTI_PROVIDER,
    models=OAuthModels(client=OAuthClient, code=AuthorizationCode, grant=Grant),
    config=_config(),
)

SINGLE_OAUTH = McpOAuth(
    provider=SINGLE_PROVIDER,
    models=OAuthModels(
        client=OAuthClient,
        code=UserAuthorizationCode,
        grant=UserGrant,
        resource_field="owner",
    ),
    config=_config(),
)


# --- a server and a mounted endpoint -----------------------------------------
#
# The transport used to be the project's to write, and every project wrote the same
# sixty lines. `mcp_endpoint` owns it now, so the suite mounts one and drives it.


class WhoamiArgs(ToolArgs):
    pass


class ShoutArgs(ToolArgs):
    text: str = Field(max_length=50)


def _whoami(context, args: WhoamiArgs) -> dict:
    return {"account": context["account"], "scope": context["scope"]}


def _shout(context, args: ShoutArgs) -> dict:
    return {"said": args.text.upper()}


TEST_REGISTRY = registry(
    Tool(
        name="whoami",
        description="Report the connected account.",
        args_model=WhoamiArgs,
        handler=_whoami,
    ),
    Tool(
        name="shout",
        description="Uppercase some text.",
        args_model=ShoutArgs,
        handler=_shout,
        writes=True,
    ),
)

TEST_SERVER = McpServer(
    name="fixture",
    version="1.0.0",
    registry=TEST_REGISTRY,
    instructions=lambda context: f"Connected as {context['account']}.",
    can_write=lambda context: context["scope"] == "read_write",
)


def _context(key: ApiKey) -> dict:
    return {"account": key.account.name, "scope": key.scope, "key": key}


def _refuse(key: ApiKey) -> str | None:
    """The project's own extra check — here, a disabled account."""
    return "This account is disabled." if key.account.name.startswith("disabled") else None


TEST_ENDPOINT = mcp_endpoint(
    server=TEST_SERVER,
    key_model=ApiKey,
    codec=CODEC,
    context=_context,
    issuer=lambda: "https://api.example.test",
    realm="Example",
    select_related=("account",),
    refuse=_refuse,
    throttle_scope="mcp-key",
    # Small enough that a test can exceed it, far above any real JSON-RPC call.
    max_body_bytes=2048,
)
