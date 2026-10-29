"""Opt-in permissions beside the access level, tools that require one, and image
results — the consent page through the minted key to a gated tool call."""

import base64
import re
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model

from drf_foundation.mcp import Image, Tool, ToolArgs, ToolResult, registry, split_scope
from drf_foundation.mcp.tools import call as call_tool
from tests.mcp_fixtures import PIXEL
from tests.test_mcp_endpoint import call, mint
from tests.test_mcp_oauth import exchange, payload_from, register, start
from tests.testapp.models import Account, ApiKey, AuthorizationCode

PREFIX = "/perm"


@pytest.fixture
def user(db):
    return get_user_model().objects.create_user(
        username="member", email="member@example.test", password="a-strong-password-123"
    )


@pytest.fixture
def account(db, user):
    account = Account.objects.create(name="Acme")
    account.members.add(user)
    return account


@pytest.fixture
def authed(client, user):
    client.force_login(user)
    return client


def _consent(authed, account, *, scope="read_write", permissions=(), requested=None):
    registered = register(authed, prefix=PREFIX)
    overrides = {"scope": requested} if requested is not None else {}
    page = start(authed, registered["client_id"], prefix=PREFIX, **overrides)
    assert page.status_code == 200, page.content
    response = authed.post(
        f"{PREFIX}/oauth/authorize",
        {
            "payload": payload_from(page.content.decode()),
            "resource": str(account.pk),
            "scope": scope,
            "permission": list(permissions),
            "action": "approve",
        },
    )
    return registered, page, response


def test_the_page_offers_each_permission_ticked_by_its_default(authed, account):
    registered = register(authed, prefix=PREFIX)
    html = start(authed, registered["client_id"], prefix=PREFIX).content.decode()
    assert "Also allow" in html
    assert re.search(r'value="files"\s+checked', html)
    assert re.search(r'value="delete"\s*>', html)


def test_a_permission_the_client_asked_for_starts_ticked(authed, account):
    registered = register(authed, prefix=PREFIX)
    page = start(authed, registered["client_id"], prefix=PREFIX, scope="read delete")
    html = page.content.decode()
    assert re.search(r'value="delete"\s+checked', html)
    # The level still comes from the request, permissions aside.
    assert re.search(r'value="read"\s+checked', html)


def test_a_provider_without_permissions_renders_none(authed, account):
    registered = register(authed)
    assert "Also allow" not in start(authed, registered["client_id"]).content.decode()


def test_the_granted_scope_carries_the_level_then_permissions_in_order(authed, account):
    registered, _, response = _consent(authed, account, permissions=("delete", "files"))
    assert response.status_code == 302, response.content
    assert AuthorizationCode.objects.get().scope == "read_write files delete"
    code = parse_qs(urlsplit(response["Location"]).query)["code"][0]
    body = exchange(authed, registered["client_id"], code, prefix=PREFIX).json()
    assert body["scope"] == "read_write files delete"
    assert ApiKey.objects.get().scope == "read_write files delete"


def test_unticking_everything_grants_the_level_alone(authed, account):
    _, _, response = _consent(authed, account, scope="read")
    assert response.status_code == 302
    assert AuthorizationCode.objects.get().scope == "read"


@pytest.mark.parametrize("permissions", [("admin",), ("files", "root")])
def test_a_permission_that_was_not_offered_is_refused(authed, account, permissions):
    _, _, response = _consent(authed, account, permissions=permissions)
    assert response.status_code == 400
    assert not AuthorizationCode.objects.exists()


def test_discovery_advertises_levels_then_permissions(client, db):
    body = client.get(f"{PREFIX}/.well-known/oauth-authorization-server").json()
    assert body["scopes_supported"] == ["read", "read_write", "files", "delete"]


def test_split_scope():
    assert split_scope("read_write files delete") == ("read_write", frozenset({"files", "delete"}))
    assert split_scope("read") == ("read", frozenset())
    assert split_scope("") == ("", frozenset())


# --- the tool gate and image results -----------------------------------------


@pytest.mark.django_db
def test_a_tool_requiring_a_permission_runs_with_it_and_returns_the_image(client):
    secret, _ = mint(scope="read files")
    result = call(client, secret, "peek")
    assert result["isError"] is False
    text, image = result["content"]
    assert text["type"] == "text" and '"pixel.png"' in text["text"]
    assert image == {
        "type": "image",
        "data": base64.b64encode(PIXEL).decode(),
        "mimeType": "image/png",
    }


@pytest.mark.django_db
def test_without_the_permission_the_tool_is_refused_naming_it(client):
    secret, _ = mint(scope="read_write delete")
    result = call(client, secret, "peek")
    assert result["isError"] is True
    assert "'files' permission" in result["content"][0]["text"]


class _Args(ToolArgs):
    pass


_GATED = registry(
    Tool(
        name="gated",
        description="d",
        args_model=_Args,
        handler=lambda ctx, args: ToolResult({}, images=(Image(b"x", "image/png"),)),
        requires="files",
    )
)


def test_a_server_that_never_says_what_a_credential_holds_grants_nothing():
    """Unset ``permissions`` fails closed, unlike unset ``can_write``: a permission
    is an opt-in, so the absence of an answer is a no."""
    result = call_tool(_GATED, None, "gated", {})
    assert result is not None and result["isError"] is True


def test_a_custom_refusal_is_used():
    result = call_tool(
        _GATED,
        None,
        "gated",
        {},
        permissions=lambda ctx: (),
        permission_refused=lambda p: f"turn on {p} in Settings",
    )
    assert result is not None and result["content"][0]["text"] == "turn on files in Settings"
