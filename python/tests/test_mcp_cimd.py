"""CIMD — URL-shaped client ids, the guarded fetcher, and the grant-time row.

Flow tests run through the same two tenancy shapes as test_mcp_oauth.py, with
the fetch seam faked (tests.mcp_fixtures.fake_cimd_fetch) so the package's own
document validation is still on the hook. The network fetcher's guards — address
vetting, redirect/size/content-type refusal, caching, the global budget — are
unit-tested against monkeypatched internals, since no test should dial out.
"""

import pytest
from django.contrib.auth import get_user_model
from django.core.cache import cache

from drf_foundation.mcp import cimd
from drf_foundation.mcp.cimd import (
    CimdError,
    fetch_document,
    is_cimd_client_id,
    validate_client_id_url,
    validate_document,
)
from tests.mcp_fixtures import (
    CIMD_DOCUMENTS,
    MULTI_PROVIDER,
    SINGLE_PROVIDER,
)
from tests.test_mcp_oauth import (
    CALLBACK,
    SHAPES,
    approve,
    exchange,
    payload_from,
    register,
    resource_id,
    start,
)
from tests.testapp.models import Account, ApiKey, OAuthClient

# Longer than the pre-CIMD 64-char client_id column, on purpose.
CIMD_ID = "https://client.example.test/oauth/connectors/example-client-metadata-document.json"
ORIGIN = "client.example.test"


def document(**overrides):
    base = {
        "client_id": CIMD_ID,
        "client_name": "Example Client",
        "client_uri": f"https://{ORIGIN}",
        "redirect_uris": [CALLBACK],
        # Claude Code's real document lists a superset of what this server
        # issues; a strict-equality check would refuse it.
        "grant_types": ["authorization_code", "refresh_token"],
        "response_types": ["code"],
        "token_endpoint_auth_method": "none",
    }
    return {**base, **overrides}


@pytest.fixture(autouse=True)
def _clean_cimd_state():
    CIMD_DOCUMENTS.clear()
    MULTI_PROVIDER.revoked.clear()
    SINGLE_PROVIDER.revoked.clear()
    cache.clear()
    yield
    CIMD_DOCUMENTS.clear()
    cache.clear()


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


def cimd_grant(authed, shape, user, account, scope="read_write"):
    prefix, _, _, _ = SHAPES[shape]
    page = start(authed, CIMD_ID, prefix=prefix)
    assert page.status_code == 200, page.content
    consent = approve(
        authed,
        payload_from(page.content.decode()),
        resource_id(shape, user, account),
        scope=scope,
        prefix=prefix,
    )
    assert consent.status_code == 302, consent.content
    from urllib.parse import parse_qs, urlsplit

    return parse_qs(urlsplit(consent["Location"]).query)["code"][0], prefix


# --- identifier URL rules -----------------------------------------------------


def test_only_https_urls_take_the_cimd_path():
    assert is_cimd_client_id("https://x.test/meta.json")
    assert not is_cimd_client_id("mcpc_abc123")
    assert not is_cimd_client_id("http://x.test/meta.json")


@pytest.mark.parametrize(
    "bad",
    [
        "http://client.test/meta.json",  # not https
        "https://client.test",  # no path
        "https://client.test/",  # root path only
        "https://user:pw@client.test/meta.json",  # userinfo
        "https://client.test/meta.json#frag",  # fragment
        "https://client.test/../meta.json",  # dot segments
        "https://client.test/meta .json",  # whitespace
        "https://client.test/" + "x" * 600,  # over the column
    ],
)
def test_client_id_url_shape_rules(bad):
    with pytest.raises(CimdError):
        validate_client_id_url(bad)


def test_a_plausible_client_id_url_passes():
    validate_client_id_url(CIMD_ID)


# --- document rules -----------------------------------------------------------


@pytest.mark.parametrize(
    "broken",
    [
        ["not a dict"],
        document(client_id="https://elsewhere.test/meta.json"),  # identity mismatch
        document(redirect_uris=[]),
        document(redirect_uris=["http://evil.test/cb"]),  # http, not loopback
        document(redirect_uris=[f"https://ok.test/{i}" for i in range(33)]),  # over cap
        document(token_endpoint_auth_method="client_secret_basic"),
        document(grant_types=["implicit"]),
        document(response_types=["token"]),
    ],
)
def test_document_rules_refuse(broken):
    with pytest.raises(CimdError):
        validate_document(CIMD_ID, broken)


def test_a_valid_document_reduces_to_a_client():
    client = validate_document(CIMD_ID, document())
    assert client.client_id == CIMD_ID
    assert client.name == "Example Client"
    assert client.origin == ORIGIN
    assert client.redirect_uris == [CALLBACK]


def test_loopback_redirects_and_missing_name_are_fine():
    # Claude Code's real document: port-less loopback redirects, name optional
    # in the draft — the origin stands in when it is absent.
    doc = document(redirect_uris=["http://localhost/callback", "http://127.0.0.1/callback"])
    del doc["client_name"]
    client = validate_document(CIMD_ID, doc)
    assert client.name == ORIGIN
    assert client.redirect_uris == ["http://localhost/callback", "http://127.0.0.1/callback"]


# --- the guarded fetcher ------------------------------------------------------


@pytest.mark.parametrize(
    "literal",
    [
        "10.0.0.1",
        "127.0.0.1",
        "169.254.1.1",
        "192.168.1.1",
        "100.64.0.1",  # CGNAT
        "224.0.0.1",  # multicast
        "0.0.0.0",
        "::1",
        "fe80::1%en0",
        "fd00::1",
        "not-an-ip",
    ],
)
def test_non_public_addresses_are_refused(literal):
    assert not cimd._is_public_address(literal)


def test_public_addresses_pass():
    assert cimd._is_public_address("93.184.216.34")
    assert cimd._is_public_address("2606:4700::1111")


def test_one_private_answer_poisons_the_whole_resolution(monkeypatch):
    def rebinding(host, port, proto):
        return [
            (2, 1, 6, "", ("93.184.216.34", 443)),
            (2, 1, 6, "", ("10.0.0.1", 443)),
        ]

    monkeypatch.setattr(cimd.socket, "getaddrinfo", rebinding)
    with pytest.raises(CimdError, match="non-public"):
        cimd._vetted_address("client.test", 443)


def test_vetting_returns_a_resolved_address(monkeypatch):
    monkeypatch.setattr(
        cimd.socket, "getaddrinfo", lambda host, port, proto: [(2, 1, 6, "", ("1.2.3.4", 443))]
    )
    assert cimd._vetted_address("client.test", 443) == "1.2.3.4"


@pytest.mark.parametrize(
    ("status", "content_type", "body", "complaint"),
    [
        (301, "application/json", b"{}", "redirects"),
        (404, "application/json", b"{}", "answered 404"),
        (200, "text/html", b"{}", "not served as JSON"),
        (200, "application/json", b"x" * (cimd.MAX_DOCUMENT_BYTES + 1), "too large"),
        (200, "application/json", b"not json", "not valid JSON"),
        (200, "application/json", b"[]", "not a JSON object"),
    ],
)
def test_fetch_refuses_non_documents(monkeypatch, status, content_type, body, complaint):
    monkeypatch.setattr(cimd, "_get", lambda url: (status, content_type, body))
    with pytest.raises(CimdError, match=complaint):
        cimd._fetch_fresh("https://client.test/meta.json")


def test_fetch_document_caches_successes_and_failures(monkeypatch):
    calls = []

    def counting(url):
        calls.append(url)
        return {"client_id": url}

    monkeypatch.setattr(cimd, "_fetch_fresh", counting)
    url = "https://client.test/meta.json"
    assert fetch_document(url) == {"client_id": url}
    assert fetch_document(url) == {"client_id": url}
    assert calls == [url]  # second hit served from cache

    def failing(url):
        calls.append(url)
        raise CimdError("nope")

    monkeypatch.setattr(cimd, "_fetch_fresh", failing)
    bad = "https://broken.test/meta.json"
    with pytest.raises(CimdError, match="nope"):
        fetch_document(bad)
    with pytest.raises(CimdError, match="nope"):
        fetch_document(bad)
    assert calls.count(bad) == 1  # the failure was cached too


def test_the_fetch_budget_is_global(monkeypatch):
    monkeypatch.setattr(cimd, "MAX_FETCHES_PER_MINUTE", 2)
    monkeypatch.setattr(cimd, "_fetch_fresh", lambda url: {"client_id": url})
    fetch_document("https://a.test/meta.json")
    fetch_document("https://b.test/meta.json")
    with pytest.raises(CimdError, match="Too many"):
        fetch_document("https://c.test/meta.json")
    # Cached documents still resolve — the budget bounds fetches, not logins.
    fetch_document("https://a.test/meta.json")


# --- discovery ----------------------------------------------------------------


def test_as_metadata_advertises_cimd(client, db):
    body = client.get("/.well-known/oauth-authorization-server").json()
    assert body["client_id_metadata_document_supported"] is True


# --- the flow, both tenancy shapes --------------------------------------------


@pytest.mark.parametrize("shape", ["multi", "single"])
def test_full_cimd_grant_without_registration(authed, user, account, shape):
    CIMD_DOCUMENTS[CIMD_ID] = document()
    prefix, _, _, _ = SHAPES[shape]

    page = start(authed, CIMD_ID, prefix=prefix)
    html = page.content.decode()
    # The verifiable origin is shown; the self-reported disclaimer is not.
    assert ORIGIN in html
    assert "self-reported" not in html
    assert OAuthClient.objects.filter(client_id=CIMD_ID).count() == 0  # no row yet

    code, prefix = cimd_grant(authed, shape, user, account)
    response = exchange(authed, CIMD_ID, code, prefix=prefix)
    assert response.status_code == 200, response.content
    token = response.json()["access_token"]
    assert token.startswith("tk_")
    key = ApiKey.objects.get(key_hash__isnull=False, revoked_at__isnull=True)
    assert key.scope == "read_write"

    row = OAuthClient.objects.get(client_id=CIMD_ID)
    assert row.name == "Example Client"
    assert row.redirect_uris == [CALLBACK]
    assert row.last_grant_at is not None


@pytest.mark.parametrize("shape", ["multi", "single"])
def test_reconnecting_replaces_the_key_and_keeps_one_row(authed, user, account, shape):
    CIMD_DOCUMENTS[CIMD_ID] = document()
    _, provider, _, _ = SHAPES[shape]
    code, prefix = cimd_grant(authed, shape, user, account)
    first = exchange(authed, CIMD_ID, code, prefix=prefix)
    assert first.status_code == 200

    # The client republishes its document with a new name; reconnect.
    CIMD_DOCUMENTS[CIMD_ID] = document(client_name="Example Client v2")
    code, prefix = cimd_grant(authed, shape, user, account)
    second = exchange(authed, CIMD_ID, code, prefix=prefix)
    assert second.status_code == 200

    assert [reason for _, reason in provider.revoked] == ["reconnected"]
    # One row per identity, refreshed in place — not one per connection.
    assert OAuthClient.objects.filter(client_id=CIMD_ID).count() == 1
    assert OAuthClient.objects.get(client_id=CIMD_ID).name == "Example Client v2"


def test_denying_creates_no_row(authed, user, account):
    CIMD_DOCUMENTS[CIMD_ID] = document()
    page = start(authed, CIMD_ID)
    response = approve(authed, payload_from(page.content.decode()), str(account.pk), action="deny")
    assert response.status_code == 302
    assert "error=access_denied" in response["Location"]
    assert OAuthClient.objects.count() == 0


def test_an_unfetchable_document_renders_an_error_page(authed, account):
    page = start(authed, "https://nowhere.test/oauth/meta.json")
    assert page.status_code == 400
    assert "Couldn" in page.content.decode()


def test_a_redirect_outside_the_document_is_refused(authed, account):
    CIMD_DOCUMENTS[CIMD_ID] = document(redirect_uris=["https://elsewhere.test/cb"])
    page = start(authed, CIMD_ID)
    assert page.status_code == 400
    assert "Invalid redirect" in page.content.decode()


def test_the_document_is_revalidated_at_consent_time(authed, account):
    CIMD_DOCUMENTS[CIMD_ID] = document()
    page = start(authed, CIMD_ID)
    payload = payload_from(page.content.decode())
    # Between page render and approve, the published document drops the redirect.
    CIMD_DOCUMENTS[CIMD_ID] = document(redirect_uris=["https://elsewhere.test/cb"])
    response = approve(authed, payload, str(account.pk))
    assert response.status_code == 400
    assert OAuthClient.objects.count() == 0


def test_token_exchange_with_an_unconsented_cimd_id_is_invalid_client(client, db):
    CIMD_DOCUMENTS[CIMD_ID] = document()
    response = exchange(client, CIMD_ID, "no-such-code")
    assert response.status_code == 401
    assert response.json()["error"] == "invalid_client"


def test_a_cimd_code_cannot_be_redeemed_by_a_registered_client(authed, user, account):
    CIMD_DOCUMENTS[CIMD_ID] = document()
    code, prefix = cimd_grant(authed, "multi", user, account)
    other = register(authed)
    response = exchange(authed, other["client_id"], code)
    assert response.status_code == 400
    assert response.json()["error"] == "invalid_grant"


def test_dynamic_registration_still_shows_the_self_reported_disclaimer(authed, account):
    registered = register(authed)
    html = start(authed, registered["client_id"]).content.decode()
    assert "self-reported" in html
