"""The endpoint factory: credential resolution, the discovery pointer, and CORS.

This is the surface that used to be each project's to write by hand, which is why
it is tested here now — the parts most easily got wrong are the ones no manual
test exercises: the ``WWW-Authenticate`` header (every hand test uses a token that
already works) and the preflight.
"""

import json

import pytest
from django.core.cache import cache
from django.test import RequestFactory

from drf_foundation.mcp.views import KeyRateThrottle, mcp_endpoint
from tests.mcp_fixtures import CODEC
from tests.testapp.models import Account, ApiKey

pytestmark = pytest.mark.django_db

ENDPOINT = "/mcp"


def mint(scope="read_write", account_name="Fixture Co"):
    account, _ = Account.objects.get_or_create(name=account_name)
    secret = CODEC.generate()
    key = ApiKey.objects.create(
        account=account,
        name="Test Client",
        key_hash=CODEC.hash(secret),
        key_prefix=CODEC.display_prefix(secret),
        scope=scope,
    )
    return secret, key


def rpc(client, secret, method, params=None, msg_id=1):
    body = {"jsonrpc": "2.0", "method": method}
    if msg_id is not None:
        body["id"] = msg_id
    if params is not None:
        body["params"] = params
    headers = {"HTTP_AUTHORIZATION": f"Bearer {secret}"} if secret else {}
    return client.post(ENDPOINT, data=json.dumps(body), content_type="application/json", **headers)


def call(client, secret, name, arguments=None):
    response = rpc(client, secret, "tools/call", {"name": name, "arguments": arguments or {}})
    return response.json()["result"]


# --- the credential ----------------------------------------------------------


def test_a_missing_credential_points_at_discovery(client):
    """The header is the whole "connect" affordance: a client that gets a bare 401
    gives up, one that gets resource_metadata starts the OAuth flow."""
    response = rpc(client, None, "initialize")

    assert response.status_code == 401
    header = response["WWW-Authenticate"]
    assert header.startswith("Bearer ")
    assert 'realm="Example"' in header
    assert "https://api.example.test/.well-known/oauth-protected-resource" in header


def test_an_unknown_token_is_refused(client):
    assert rpc(client, "tk_nope", "initialize").status_code == 401


def test_a_revoked_key_stops_working(client):
    secret, key = mint()
    assert rpc(client, secret, "ping").status_code == 200

    key.revoked_at = key.created_at
    key.save(update_fields=["revoked_at"])

    assert rpc(client, secret, "ping").status_code == 401


def test_the_projects_own_refusal_is_honored(client):
    """`refuse` covers what the key cannot know — here, a disabled account."""
    secret, _ = mint(account_name="disabled-co")

    response = rpc(client, secret, "ping")

    assert response.status_code == 401
    assert "disabled" in response.json()["error_description"]


def test_using_a_key_records_last_used(client):
    secret, key = mint()
    assert key.last_used_at is None

    rpc(client, secret, "ping")

    key.refresh_from_db()
    assert key.last_used_at is not None


# --- the protocol, through the view ------------------------------------------


def test_initialize_carries_the_projects_context(client):
    secret, _ = mint()

    result = rpc(client, secret, "initialize").json()["result"]

    assert result["serverInfo"]["name"] == "fixture"
    assert "Fixture Co" in result["instructions"]


def test_a_notification_gets_202_and_no_body(client):
    secret, _ = mint()

    response = rpc(client, secret, "notifications/initialized", msg_id=None)

    assert response.status_code == 202
    assert not response.content


def test_tools_list_and_call_round_trip(client):
    secret, _ = mint()

    listed = rpc(client, secret, "tools/list").json()["result"]["tools"]
    assert {t["name"] for t in listed} == {"whoami", "shout"}

    result = call(client, secret, "whoami")
    payload = json.loads(result["content"][0]["text"])
    assert payload["account"] == "Fixture Co"


def test_the_write_gate_uses_the_projects_scope(client):
    secret, _ = mint(scope="read")

    result = call(client, secret, "shout", {"text": "hi"})

    assert result["isError"] is True
    assert "read-only" in result["content"][0]["text"].lower()


# --- transport concerns ------------------------------------------------------


def test_preflight_is_answered_without_a_credential(client):
    response = client.options(ENDPOINT)

    assert response.status_code == 204
    assert response["Access-Control-Allow-Origin"] == "*"
    assert "Authorization" in response["Access-Control-Allow-Headers"]


def test_the_401_is_readable_cross_origin(client):
    """A browser-based client cannot read WWW-Authenticate unless it is exposed."""
    response = rpc(client, None, "initialize")

    assert response["Access-Control-Allow-Origin"] == "*"
    assert "WWW-Authenticate" in response["Access-Control-Expose-Headers"]


def test_get_is_not_allowed(client):
    assert client.get(ENDPOINT).status_code == 405


# --- throttling --------------------------------------------------------------


@pytest.fixture
def throttle_rate(monkeypatch):
    """DRF binds THROTTLE_RATES as a class attribute at import, so a settings
    override does not reach it — the class attribute is what has to be patched."""

    def _set(rate):
        monkeypatch.setattr(
            KeyRateThrottle,
            "THROTTLE_RATES",
            {**KeyRateThrottle.THROTTLE_RATES, "mcp-key": rate},
        )
        cache.clear()

    yield _set
    cache.clear()


def test_requests_are_throttled_per_key(client, throttle_rate):
    throttle_rate("2/min")
    secret, _ = mint()

    assert rpc(client, secret, "ping").status_code == 200
    assert rpc(client, secret, "ping").status_code == 200
    limited = rpc(client, secret, "ping")

    assert limited.status_code == 429
    assert limited.json()["error"] == "rate_limited"


def test_one_keys_limit_does_not_affect_another(client, throttle_rate):
    """Keyed on the credential, not the IP — an MCP client calls from its
    provider's shared egress addresses."""
    throttle_rate("1/min")
    first, _ = mint(account_name="First Co")
    second, _ = mint(account_name="Second Co")

    assert rpc(client, first, "ping").status_code == 200
    assert rpc(client, first, "ping").status_code == 429
    assert rpc(client, second, "ping").status_code == 200


def test_the_budget_rides_every_response(client, throttle_rate):
    """Not just the 429. A client that only learns its ceiling by hitting it cannot
    pace itself, and the 429 arrives mid-conversation where it reads as a broken tool."""
    throttle_rate("10/min")
    secret, _ = mint()

    response = rpc(client, secret, "ping")

    assert response.status_code == 200
    assert response["X-RateLimit-Limit"] == "10"
    assert response["X-RateLimit-Remaining"] == "9"
    assert int(response["X-RateLimit-Reset"]) > 0


def test_the_budget_counts_down(client, throttle_rate):
    throttle_rate("10/min")
    secret, _ = mint()

    remaining = [rpc(client, secret, "ping")["X-RateLimit-Remaining"] for _ in range(3)]

    assert remaining == ["9", "8", "7"]


def test_a_throttled_response_says_when_to_retry(client, throttle_rate):
    throttle_rate("1/min")
    secret, _ = mint()
    rpc(client, secret, "ping")

    limited = rpc(client, secret, "ping")

    assert limited.status_code == 429
    assert limited["X-RateLimit-Remaining"] == "0"
    assert 0 < int(limited["Retry-After"]) <= 61


def test_the_budget_is_readable_cross_origin(client, throttle_rate):
    """A browser-based MCP client sees no header it cannot read — the endpoint is
    wildcard-CORS, so anything useful has to be named in Expose-Headers."""
    throttle_rate("10/min")
    secret, _ = mint()

    exposed = rpc(client, secret, "ping")["Access-Control-Expose-Headers"]

    for header in ("X-RateLimit-Limit", "X-RateLimit-Remaining", "X-RateLimit-Reset"):
        assert header in exposed


# --- body size ---------------------------------------------------------------


def test_an_oversized_body_is_refused(client):
    """413, but as a JSON-RPC envelope: the caller is an MCP client, and a body it
    cannot parse is indistinguishable from the server being broken."""
    secret, _ = mint()
    padding = "x" * 4096

    response = client.post(
        ENDPOINT,
        data=json.dumps({"jsonrpc": "2.0", "id": 1, "method": "ping", "params": {"pad": padding}}),
        content_type="application/json",
        HTTP_AUTHORIZATION=f"Bearer {secret}",
    )

    assert response.status_code == 413
    body = response.json()
    assert body["jsonrpc"] == "2.0"
    assert body["error"]["code"] == -32600
    assert "too large" in body["error"]["message"].lower()


def test_a_body_under_the_cap_is_fine(client):
    secret, _ = mint()

    response = rpc(client, secret, "ping", {"pad": "x" * 512})

    assert response.status_code == 200


def test_a_disabled_scope_serves_normally_and_reports_no_budget(client, throttle_rate):
    """`DEFAULT_THROTTLE_RATES[scope] = None` is DRF's idiom for switching a bucket
    off, and what test settings usually do. DRF short-circuits before recording any
    history, so there is no budget — the endpoint must still answer, not 500."""
    throttle_rate(None)
    secret, _ = mint()

    response = rpc(client, secret, "ping")

    assert response.status_code == 200
    assert "X-RateLimit-Limit" not in response
    assert "X-RateLimit-Remaining" not in response


def test_the_realm_can_be_omitted():
    """`resource_metadata` alone is all an RFC 9728 client reads, and a project already
    live without a realm should not have to change its handshake bytes to adopt this."""
    from tests.mcp_fixtures import CODEC as _CODEC
    from tests.mcp_fixtures import TEST_SERVER, _context
    from tests.testapp.models import ApiKey as _ApiKey

    view = mcp_endpoint(
        server=TEST_SERVER,
        key_model=_ApiKey,
        codec=_CODEC,
        context=_context,
        issuer=lambda: "https://api.example.test",
        realm=None,
    )
    request = RequestFactory().post("/mcp", data="{}", content_type="application/json")

    header = view(request)["WWW-Authenticate"]

    assert header.startswith("Bearer resource_metadata=")
    assert "realm" not in header
