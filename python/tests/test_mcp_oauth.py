"""The OAuth 2.1 handshake, asserted against both tenancy shapes.

Most tests are parametrized over a multi-tenant surface (grants against an Account
the user belongs to) and a single-tenant one (grants against the user). They share
every assertion, which is the claim the extraction rests on: the per-user case is
the multi-tenant case with one resource, not a second code path.
"""

import base64
import hashlib
import json
import re
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from django.contrib.auth import get_user_model
from django.utils import timezone

from drf_foundation.mcp import (
    McpOAuth,
    OAuthConfig,
    OAuthModels,
    Resource,
    Scope,
    TokenCodec,
    login_redirect,
    redirect_uri_allowed,
)
from tests.mcp_fixtures import (
    CODEC,
    KEY_QUOTA,
    MULTI_PROVIDER,
    SINGLE_PROVIDER,
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

CALLBACK = "https://client.example.test/callback"
VERIFIER = "z" * 64
CHALLENGE = (
    base64.urlsafe_b64encode(hashlib.sha256(VERIFIER.encode()).digest()).rstrip(b"=").decode()
)

# (url prefix, provider, code model, grant model) per shape.
SHAPES = {
    "multi": ("", MULTI_PROVIDER, AuthorizationCode, Grant),
    "single": ("/single", SINGLE_PROVIDER, UserAuthorizationCode, UserGrant),
}


@pytest.fixture(autouse=True)
def _reset_providers():
    MULTI_PROVIDER.revoked.clear()
    SINGLE_PROVIDER.revoked.clear()


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


def register(client, redirect_uris=(CALLBACK,), name="Test Client", prefix=""):
    response = client.post(
        f"{prefix}/oauth/register",
        data=json.dumps({"client_name": name, "redirect_uris": list(redirect_uris)}),
        content_type="application/json",
    )
    assert response.status_code == 201, response.content
    return response.json()


def resource_id(shape, user, account):
    return str(user.pk) if shape == "single" else str(account.pk)


def start(authed, client_id, prefix="", **overrides):
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": CALLBACK,
        "code_challenge": CHALLENGE,
        "code_challenge_method": "S256",
        **overrides,
    }
    return authed.get(f"{prefix}/oauth/authorize", params)


def payload_from(html: str) -> str:
    match = re.search(r'name="payload" value="([^"]+)"', html)
    assert match, html[:400]
    return match.group(1)


def approve(authed, payload, resource, scope="read_write", prefix="", action="approve"):
    return authed.post(
        f"{prefix}/oauth/authorize",
        {"payload": payload, "resource": resource, "scope": scope, "action": action},
    )


def exchange(client, client_id, code, prefix="", overrides=None):
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "code_verifier": VERIFIER,
        "redirect_uri": CALLBACK,
        **(overrides or {}),
    }
    return client.post(f"{prefix}/oauth/token", data)


def full_grant(authed, shape, user, account, scope="read_write"):
    prefix, _, _, _ = SHAPES[shape]
    registered = register(authed, prefix=prefix)
    page = start(authed, registered["client_id"], prefix=prefix)
    assert page.status_code == 200, page.content
    consent = approve(
        authed,
        payload_from(page.content.decode()),
        resource_id(shape, user, account),
        scope=scope,
        prefix=prefix,
    )
    assert consent.status_code == 302, consent.content
    code = parse_qs(urlsplit(consent["Location"]).query)["code"][0]
    return registered, code, prefix


# --- discovery ---------------------------------------------------------------


def test_protected_resource_metadata_points_at_the_endpoint_and_itself(client, db):
    body = client.get("/.well-known/oauth-protected-resource").json()
    assert body["resource"] == "https://api.example.test/mcp"
    assert body["authorization_servers"] == ["https://api.example.test"]
    assert body["scopes_supported"] == ["read", "read_write"]
    assert body["bearer_methods_supported"] == ["header"]


def test_protected_resource_metadata_is_served_at_the_suffixed_path_too(client, db):
    # RFC 9728 §3.1 — claude.ai probes the path-suffixed spelling.
    plain = client.get("/.well-known/oauth-protected-resource").json()
    assert client.get("/.well-known/oauth-protected-resource/mcp").json() == plain


def test_authorization_server_metadata_advertises_only_what_is_implemented(client, db):
    body = client.get("/.well-known/oauth-authorization-server").json()
    assert body["issuer"] == "https://api.example.test"
    assert body["authorization_endpoint"] == "https://api.example.test/oauth/authorize"
    assert body["token_endpoint"] == "https://api.example.test/oauth/token"
    assert body["grant_types_supported"] == ["authorization_code"]
    # PKCE S256 only, public clients only — advertising more would invite it.
    assert body["code_challenge_methods_supported"] == ["S256"]
    assert body["token_endpoint_auth_methods_supported"] == ["none"]


def test_discovery_is_cacheable_and_cross_origin_readable(client, db):
    for url in (
        "/.well-known/oauth-protected-resource",
        "/.well-known/oauth-authorization-server",
    ):
        response = client.get(url)
        assert response["Cache-Control"] == "public, max-age=3600"
        assert response["Access-Control-Allow-Origin"] == "*"


# --- dynamic client registration ---------------------------------------------


def test_registration_issues_a_public_client(client, db):
    body = register(client)
    assert body["client_id"].startswith("mcpc_")
    assert body["token_endpoint_auth_method"] == "none"
    # No secret is issued, which is what makes open registration safe.
    assert "client_secret" not in body


@pytest.mark.parametrize(
    "redirect_uris",
    [
        [],
        ["http://evil.example.test/cb"],  # plain http, not loopback
        ["https://ok.test/cb#frag"],  # fragment
        ["ftp://ok.test/cb"],
        ["https://ok.test/" + "x" * 600],
        [f"https://ok.test/{i}" for i in range(11)],  # over the cap
        ["not a url"],
        [123],
    ],
)
def test_registration_rejects_unusable_redirect_uris(client, db, redirect_uris):
    response = client.post(
        "/oauth/register",
        data=json.dumps({"redirect_uris": redirect_uris}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_redirect_uri"


def test_registration_accepts_loopback_over_plain_http(client, db):
    body = register(client, redirect_uris=["http://127.0.0.1:8976/cb"])
    assert body["redirect_uris"] == ["http://127.0.0.1:8976/cb"]


def test_registration_rejects_junk_bodies(client, db):
    for body, ctype in [(b"not json", "application/json"), (b"[]", "application/json")]:
        response = client.post("/oauth/register", data=body, content_type=ctype)
        assert response.status_code == 400
        assert response.json()["error"] == "invalid_client_metadata"


def test_registration_refuses_an_oversized_body(client, db):
    response = client.post(
        "/oauth/register",
        data=json.dumps({"client_name": "x" * 20000, "redirect_uris": [CALLBACK]}),
        content_type="application/json",
    )
    assert response.status_code == 400
    assert "too large" in response.json()["error_description"]


# --- authorize + consent ------------------------------------------------------


def test_consent_page_renders_a_picker_when_there_is_a_choice(authed, account, user):
    second = Account.objects.create(name="Beta Co")
    second.members.add(user)
    registered = register(authed)

    html = start(authed, registered["client_id"]).content.decode()

    assert "Test Client" in html
    assert 'name="payload"' in html
    assert '<select id="resource"' in html
    assert "Acme" in html and "Beta Co" in html


@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_single_resource_renders_no_picker_in_either_shape(authed, account, user, shape):
    # The per-user app is the multi-tenant app with one resource — including a
    # multi-tenant user who happens to belong to exactly one. No dead UI either way.
    prefix, _, _, _ = SHAPES[shape]
    registered = register(authed, prefix=prefix)

    html = start(authed, registered["client_id"], prefix=prefix).content.decode()

    assert '<select id="resource"' not in html
    assert f'name="resource" value="{resource_id(shape, user, account)}"' in html


def test_an_anonymous_visitor_is_sent_to_log_in(client, db):
    registered = register(client)
    response = start(client, registered["client_id"])
    assert response.status_code == 302
    assert response["Location"].startswith("https://app.example.test/login?next=")


def test_a_user_with_nothing_to_connect_gets_told_so(authed, db):
    # No Account membership: the multi-tenant provider has nothing to offer.
    registered = register(authed)
    response = start(authed, registered["client_id"])
    assert response.status_code == 400
    assert "Nothing to connect" in response.content.decode()


def test_an_unregistered_client_is_rendered_not_redirected(authed, account):
    response = start(authed, "mcpc_never_registered")
    assert response.status_code == 400
    assert "Unknown app" in response.content.decode()


def test_an_unregistered_redirect_is_rendered_not_bounced(authed, account):
    # Never redirect to a target the client did not register — that is an open
    # redirect, and it is reachable before any consent has happened.
    registered = register(authed)
    response = start(authed, registered["client_id"], redirect_uri="https://evil.test/cb")
    assert response.status_code == 400
    assert "Invalid redirect" in response.content.decode()
    assert "Location" not in response


@pytest.mark.parametrize(
    ("overrides", "error"),
    [
        ({"response_type": "token"}, "unsupported_response_type"),
        ({"code_challenge": ""}, "invalid_request"),
        ({"code_challenge": "x" * 200}, "invalid_request"),
        ({"code_challenge_method": "plain"}, "invalid_request"),
    ],
)
def test_a_bad_authorization_request_redirects_the_error_back(authed, account, overrides, error):
    registered = register(authed)
    response = start(authed, registered["client_id"], state="st8", **overrides)
    assert response.status_code == 302
    query = parse_qs(urlsplit(response["Location"]).query)
    assert query["error"] == [error]
    assert query["state"] == ["st8"], "state must survive the error path too"


def test_declining_redirects_back_with_access_denied(authed, account, user):
    registered = register(authed)
    page = start(authed, registered["client_id"], state="st9")
    response = approve(authed, payload_from(page.content.decode()), str(account.pk), action="deny")
    query = parse_qs(urlsplit(response["Location"]).query)
    assert query["error"] == ["access_denied"]
    assert query["state"] == ["st9"]
    assert not AuthorizationCode.objects.exists()


def test_a_tampered_or_expired_consent_payload_is_refused(authed, account):
    response = approve(authed, "not-a-signed-payload", str(account.pk))
    assert response.status_code == 400
    assert "Expired" in response.content.decode()


def test_an_invalid_scope_is_refused(authed, account, user):
    registered = register(authed)
    page = start(authed, registered["client_id"])
    response = approve(authed, payload_from(page.content.decode()), str(account.pk), scope="admin")
    assert response.status_code == 400
    assert not AuthorizationCode.objects.exists()


def test_the_narrower_scope_is_preselected_when_that_is_what_was_asked_for(authed, account):
    registered = register(authed)
    page = start(authed, registered["client_id"], scope="read")
    html = page.content.decode()
    assert re.search(r'value="read"\s+checked', html)


# --- the authorization check --------------------------------------------------


@pytest.mark.parametrize("shape", list(SHAPES))
def test_resolve_returning_none_denies_before_anything_is_minted(authed, account, user, shape):
    """The seam's one security-critical method: a provider that says no must stop
    the flow dead — no key, no grant row, no code marked used."""
    prefix, _, code_model, grant_model = SHAPES[shape]
    registered = register(authed, prefix=prefix)
    page = start(authed, registered["client_id"], prefix=prefix)

    # A resource this user has no claim on (someone else's account / another user).
    stranger = Account.objects.create(name="Not Mine")
    other_id = str(stranger.pk) if shape == "multi" else str(user.pk + 999)
    response = approve(authed, payload_from(page.content.decode()), other_id, prefix=prefix)

    assert response.status_code == 403
    assert not code_model.objects.exists()
    assert not grant_model.objects.exists()
    assert not ApiKey.objects.exists()


def test_a_malformed_resource_id_denies_rather_than_erroring(authed, account):
    registered = register(authed)
    page = start(authed, registered["client_id"])
    response = approve(authed, payload_from(page.content.decode()), "../../etc/passwd")
    assert response.status_code == 403


# --- token exchange -----------------------------------------------------------


@pytest.mark.parametrize("shape", list(SHAPES))
def test_the_full_grant_mints_exactly_one_usable_key(authed, account, user, shape):
    registered, code, prefix = full_grant(authed, shape, user, account)
    _, _, code_model, grant_model = SHAPES[shape]

    response = exchange(authed, registered["client_id"], code, prefix=prefix)
    body = response.json()

    assert response.status_code == 200
    assert body["token_type"] == "Bearer"
    assert body["scope"] == "read_write"
    assert body["access_token"].startswith("tk_")
    # The secret is not what was stored.
    key = ApiKey.objects.get()
    assert key.key_hash == CODEC.hash(body["access_token"])
    assert grant_model.objects.count() == 1
    assert code_model.objects.get().issued_key_id == key.pk


@pytest.mark.parametrize("shape", list(SHAPES))
def test_a_replayed_code_is_refused_and_revokes_what_it_minted(authed, account, user, shape):
    # OAuth 2.1: a reused code means the code leaked, so the credential it already
    # produced cannot be trusted either.
    registered, code, prefix = full_grant(authed, shape, user, account)
    first = exchange(authed, registered["client_id"], code, prefix=prefix)
    assert first.status_code == 200

    replay = exchange(authed, registered["client_id"], code, prefix=prefix)

    assert replay.status_code == 400
    assert replay.json()["error"] == "invalid_grant"
    assert ApiKey.objects.get().revoked_at is not None


@pytest.mark.parametrize("shape", list(SHAPES))
def test_reconnecting_replaces_the_previous_key_rather_than_stacking(authed, account, user, shape):
    registered, code, prefix = full_grant(authed, shape, user, account)
    exchange(authed, registered["client_id"], code, prefix=prefix)
    first = ApiKey.objects.get()

    # Same client, same resource, second time around.
    page = start(authed, registered["client_id"], prefix=prefix)
    consent = approve(
        authed,
        payload_from(page.content.decode()),
        resource_id(shape, user, account),
        prefix=prefix,
    )
    second_code = parse_qs(urlsplit(consent["Location"]).query)["code"][0]
    exchange(authed, registered["client_id"], second_code, prefix=prefix)

    first.refresh_from_db()
    assert first.revoked_at is not None
    assert ApiKey.objects.filter(revoked_at__isnull=True).count() == 1


def test_mint_refused_is_reported_to_the_client(authed, account, user):
    for i in range(KEY_QUOTA):
        ApiKey.objects.create(
            account=account, name=f"k{i}", key_hash=f"h{i}", key_prefix="tk_", scope="read"
        )
    registered, code, prefix = full_grant(authed, "multi", user, account)

    response = exchange(authed, registered["client_id"], code)

    assert response.status_code == 400
    assert str(KEY_QUOTA) in response.json()["error_description"]
    assert not Grant.objects.exists()


def test_pkce_verification_actually_verifies(authed, account, user):
    registered, code, _ = full_grant(authed, "multi", user, account)
    response = exchange(
        authed, registered["client_id"], code, overrides={"code_verifier": "w" * 64}
    )
    assert response.status_code == 400
    assert "PKCE" in response.json()["error_description"]
    assert not ApiKey.objects.exists()


def test_an_expired_code_is_refused(authed, account, user):
    registered, code, _ = full_grant(authed, "multi", user, account)
    AuthorizationCode.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
    response = exchange(authed, registered["client_id"], code)
    assert response.status_code == 400
    assert "expired" in response.json()["error_description"]


def test_a_mismatched_redirect_uri_is_refused(authed, account, user):
    registered, code, _ = full_grant(authed, "multi", user, account)
    response = exchange(
        authed,
        registered["client_id"],
        code,
        overrides={"redirect_uri": "https://client.example.test/other"},
    )
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_a_code_cannot_be_redeemed_by_a_different_client(authed, account, user):
    registered, code, _ = full_grant(authed, "multi", user, account)
    other = register(authed, name="Other")
    response = exchange(authed, other["client_id"], code)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


@pytest.mark.parametrize(
    ("overrides", "status", "error"),
    [
        ({"grant_type": "client_credentials"}, 400, "unsupported_grant_type"),
        ({"code": ""}, 400, "invalid_request"),
        ({"code_verifier": ""}, 400, "invalid_request"),
        ({"redirect_uri": ""}, 400, "invalid_request"),
        ({"client_id": "mcpc_nope"}, 401, "invalid_client"),
        ({"code": "made-up"}, 400, "invalid_grant"),
    ],
)
def test_token_endpoint_negative_matrix(authed, account, user, overrides, status, error):
    registered, code, _ = full_grant(authed, "multi", user, account)
    response = exchange(authed, registered["client_id"], code, overrides=overrides)
    assert response.status_code == status
    assert response.json()["error"] == error


# --- loopback redirect matching -----------------------------------------------


def test_loopback_redirects_match_on_any_port(authed, db, user):
    account = Account.objects.create(name="Loop")
    account.members.add(user)
    registered = register(authed, redirect_uris=["http://127.0.0.1:1111/cb"])
    page = start(authed, registered["client_id"], redirect_uri="http://127.0.0.1:65000/cb")
    # RFC 8252 §7.3: a native client binds a fresh port each session.
    assert page.status_code == 200


@pytest.mark.parametrize(
    ("candidate", "registered", "allowed"),
    [
        ("https://a.test/cb", ["https://a.test/cb"], True),
        ("https://a.test/cb", ["https://a.test/other"], False),
        ("http://127.0.0.1:9/cb", ["http://127.0.0.1:1/cb"], True),
        ("http://localhost:9/cb", ["http://127.0.0.1:1/cb"], False),  # host still matters
        ("http://127.0.0.1:9/other", ["http://127.0.0.1:1/cb"], False),  # path still matters
        ("http://127.0.0.1:9/cb?x=1", ["http://127.0.0.1:1/cb"], False),  # query too
        ("https://a.test:9/cb", ["https://a.test:1/cb"], False),  # https pins the port
    ],
)
def test_redirect_uri_matching_rules(candidate, registered, allowed):
    assert redirect_uri_allowed(candidate, registered) is allowed


# --- provider validation ------------------------------------------------------


class _Incomplete:
    scopes = (Scope(value="read", label="Read"),)

    def resources(self, user):
        return []

    # resolve/mint/replace_previous/revoke deliberately absent.


def test_an_incomplete_provider_fails_at_construction_not_first_request():
    with pytest.raises(TypeError) as exc:
        McpOAuth(
            provider=_Incomplete(),
            models=OAuthModels(client=OAuthClient, code=AuthorizationCode, grant=Grant),
            config=OAuthConfig(
                issuer=lambda: "https://x.test",
                resource_name="X",
                codec=TokenCodec(prefix="tk_"),
                login_url=lambda request: "/login",
            ),
        )
    message = str(exc.value)
    assert "resolve" in message and "mint" in message


def test_a_provider_with_no_scopes_is_refused():
    class NoScopes(_Incomplete):
        scopes = ()

        def resolve(self, user, resource_id):
            return None

        def mint(self, **kwargs):
            return "", None

        def replace_previous(self, **kwargs):
            return None

        def revoke(self, key, *, reason):
            return None

    with pytest.raises(TypeError, match="at least one Scope"):
        McpOAuth(
            provider=NoScopes(),
            models=OAuthModels(client=OAuthClient, code=AuthorizationCode, grant=Grant),
            config=OAuthConfig(
                issuer=lambda: "https://x.test",
                resource_name="X",
                codec=TokenCodec(prefix="tk_"),
                login_url=lambda request: "/login",
            ),
        )


def test_resource_dataclass_carries_the_projects_own_row(db):
    account = Account.objects.create(name="Held")
    resource = Resource(id=str(account.pk), label=account.name, obj=account)
    assert resource.obj is account


# --- consumer-wiring guardrails ----------------------------------------------
#
# Each of these covers a mistake that is otherwise invisible until a real person
# clicks Approve on a real deployment. They are cheap here and expensive there.


def _config(**overrides):
    defaults = {
        "issuer": lambda: "https://x.test",
        "resource_name": "X",
        "codec": TokenCodec(prefix="tk_"),
        "login_url": lambda request: "/login",
    }
    return OAuthConfig(**{**defaults, **overrides})


def test_login_redirect_defaults_to_login_but_takes_the_apps_real_route(rf):
    """An SPA's sign-in route is not a constant. Getting it wrong renders nothing
    and kills the connection with no error anywhere, so it is an argument."""
    request = rf.get("/oauth/authorize?client_id=abc")

    default = login_redirect("https://app.example.test")(request)
    assert default.startswith("https://app.example.test/login?next=")

    custom = login_redirect("https://app.example.test", path="/auth")(request)
    assert custom.startswith("https://app.example.test/auth?next=")

    renamed = login_redirect("https://app.example.test", path="/signin", next_param="return_to")(
        request
    )
    assert renamed.startswith("https://app.example.test/signin?return_to=")

    # The return target round-trips the authorize request, so the flow resumes.
    from urllib.parse import unquote

    assert "/oauth/authorize" in unquote(custom)


def test_login_redirect_rejects_a_path_that_is_not_a_path():
    with pytest.raises(ValueError, match="must start with"):
        login_redirect("https://app.example.test", path="auth")


def test_a_resource_field_colliding_with_the_bases_user_is_refused_at_construction():
    """`user` is the single most likely name for a per-user app to reach for, and
    AbstractAuthorizationCode already owns it. Left unchecked this is a TypeError
    about duplicate kwargs, raised from inside the consent view."""
    with pytest.raises(TypeError, match="collides"):
        McpOAuth(
            provider=SINGLE_PROVIDER,
            models=OAuthModels(
                client=OAuthClient,
                code=UserAuthorizationCode,
                grant=UserGrant,
                resource_field="user",
            ),
            config=_config(),
        )


def test_the_collision_message_names_the_way_out():
    with pytest.raises(TypeError) as excinfo:
        McpOAuth(
            provider=SINGLE_PROVIDER,
            models=OAuthModels(
                client=OAuthClient,
                code=UserAuthorizationCode,
                grant=UserGrant,
                resource_field="user",
            ),
            config=_config(),
        )
    message = str(excinfo.value)
    assert "owner" in message
    assert "completed consent" in message


def test_a_resource_field_no_model_declares_is_refused_at_construction():
    with pytest.raises(TypeError, match="is not a field on"):
        McpOAuth(
            provider=MULTI_PROVIDER,
            models=OAuthModels(
                client=OAuthClient,
                code=AuthorizationCode,
                grant=Grant,
                resource_field="workspace",
            ),
            config=_config(),
        )


def test_both_working_shapes_still_construct():
    """The guardrails must not reject either tenancy shape the package supports."""
    McpOAuth(
        provider=MULTI_PROVIDER,
        models=OAuthModels(client=OAuthClient, code=AuthorizationCode, grant=Grant),
        config=_config(),
    )
    McpOAuth(
        provider=SINGLE_PROVIDER,
        models=OAuthModels(
            client=OAuthClient,
            code=UserAuthorizationCode,
            grant=UserGrant,
            resource_field="owner",
        ),
        config=_config(),
    )


def test_the_error_page_names_the_projects_product_not_the_packages(authed, account):
    """The default error template must not show another product's name."""
    response = start(authed, "no-such-client")

    assert response.status_code == 400
    body = response.content.decode()
    assert "adulting" not in body.lower()
    # resource_name comes from the project's own OAuthConfig.
    assert "Example" in body
