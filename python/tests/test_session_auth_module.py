"""drf_foundation.session_auth against Django's *default* username User — the module
is user-model-agnostic (projects bring their own email-login model), and `authenticate`
reads whatever the model's USERNAME_FIELD is."""

import pytest
from django.contrib.auth.models import User
from django.core.cache import cache
from rest_framework.test import APIClient

PASSWORD = "a-strong-password-123"


@pytest.fixture(autouse=True)
def session_auth_settings_applied(settings):
    """Opt this module into the session-auth settings block.

    Not global in `django_settings.py`: CSRF_USE_SESSIONS raises on any request whose
    middleware stack has no SessionMiddleware, and other modules here drive views
    through a trimmed stack.
    """
    from drf_foundation.settings_helpers import session_auth_settings

    for key, value in session_auth_settings(cross_origin_spa=True).items():
        setattr(settings, key, value)


@pytest.fixture(autouse=True)
def clear_throttle_state():
    """Throttle counters live in the (locmem) cache and outlive a test — without this,
    a module's worth of logins trips the per-IP login rate and later tests 429."""
    cache.clear()
    yield
    cache.clear()


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(username="pat@example.com", password=PASSWORD)


@pytest.fixture
def client() -> APIClient:
    """CSRF checks on, unlike the default test client — this module is about proving
    they fire, so silencing them would make every assertion here vacuous."""
    return APIClient(enforce_csrf_checks=True)


def bootstrap(client: APIClient) -> str:
    response = client.get("/api/session/csrf")
    assert response.status_code == 200
    return response.json()["data"]["csrf_token"]


def sign_in(client: APIClient, username: str = "pat@example.com") -> str:
    """Log in and return the post-login CSRF token the response hands back."""
    response = client.post(
        "/api/session/login",
        {"email": username, "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=bootstrap(client),
    )
    assert response.status_code == 200, response.content
    return response.json()["data"]["csrf_token"]


def test_login_establishes_a_session_and_returns_no_token_values(user, client):
    response = client.post(
        "/api/session/login",
        {"email": "pat@example.com", "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=bootstrap(client),
    )

    assert response.status_code == 200
    assert "sessionid" in response.cookies
    # The session cookie is the credential; nothing token-shaped goes on the wire.
    assert set(response.json()["data"]) == {"csrf_token"}


def test_session_cookie_is_httponly(user, client):
    client.post(
        "/api/session/login",
        {"email": "pat@example.com", "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=bootstrap(client),
    )
    # The whole point of the module: page JavaScript cannot read this cookie, so an
    # XSS cannot exfiltrate a replayable credential.
    assert client.cookies["sessionid"]["httponly"]


def test_wrong_password_authenticates_nobody(user, client):
    token = bootstrap(client)
    response = client.post(
        "/api/session/login",
        {"email": "pat@example.com", "password": "wrong-password"},
        format="json",
        HTTP_X_CSRFTOKEN=token,
    )

    assert response.status_code == 401
    # A session cookie may well exist — under CSRF_USE_SESSIONS the bootstrap above
    # created an anonymous session to hold the CSRF secret. What matters is that it
    # carries no identity.
    after = client.post("/api/session/protected", {}, format="json", HTTP_X_CSRFTOKEN=token)
    assert after.status_code == 401


def test_login_requires_a_csrf_token(user, client):
    """Login CSRF is a real attack (victim silently signed into the attacker's
    account), and DRF exempts APIViews from the middleware — so the view opts back in."""
    response = client.post(
        "/api/session/login",
        {"email": "pat@example.com", "password": PASSWORD},
        format="json",
    )

    assert response.status_code == 403
    assert "sessionid" not in response.cookies


def test_session_authenticates_subsequent_requests(user, client):
    token = sign_in(client)

    response = client.post("/api/session/protected", {}, format="json", HTTP_X_CSRFTOKEN=token)

    assert response.status_code == 200


def test_mutation_without_csrf_token_is_rejected(user, client):
    sign_in(client)

    response = client.post("/api/session/protected", {}, format="json")

    assert response.status_code == 403


def test_login_rotates_the_csrf_token(user, client):
    """`django.contrib.auth.login` calls `rotate_token`, so a token minted before
    sign-in is dead afterwards — which is why the login response returns the new one."""
    pre_login = bootstrap(client)
    client.post(
        "/api/session/login",
        {"email": "pat@example.com", "password": PASSWORD},
        format="json",
        HTTP_X_CSRFTOKEN=pre_login,
    )

    stale = client.post("/api/session/protected", {}, format="json", HTTP_X_CSRFTOKEN=pre_login)

    assert stale.status_code == 403


def test_logout_ends_the_session(user, client):
    token = sign_in(client)

    logout = client.post("/api/session/logout", {}, format="json", HTTP_X_CSRFTOKEN=token)
    assert logout.status_code == 200

    # Cookie cleared, and the old session no longer authenticates anything.
    assert client.cookies["sessionid"].value == ""
    after = client.post("/api/session/protected", {}, format="json", HTTP_X_CSRFTOKEN=token)
    assert after.status_code in (401, 403)


def test_logout_without_a_session_still_succeeds(db, client):
    response = client.post(
        "/api/session/logout", {}, format="json", HTTP_X_CSRFTOKEN=bootstrap(client)
    )

    assert response.status_code == 200


def test_header_authenticated_request_skips_csrf(user, db):
    """API keys and other header credentials must not be dragged into CSRF: they are
    not sent automatically by browsers, so there is nothing to forge."""
    from rest_framework.authtoken.models import Token
    from rest_framework.test import APIClient

    token, _ = Token.objects.get_or_create(user=user)
    authed = APIClient()
    authed.credentials(HTTP_AUTHORIZATION=f"Token {token.key}")

    response = authed.post("/api/session/protected", {}, format="json")

    assert response.status_code == 200


def test_csrf_bootstrap_is_anonymous_and_returns_a_token(db, client):
    response = client.get("/api/session/csrf")

    assert response.status_code == 200
    assert response.json()["data"]["csrf_token"]


def test_stock_session_auth_sends_no_www_authenticate_challenge():
    """Why a session-only stack answers **403** rather than 401 for an unauthenticated
    request: DRF takes the status from the first authenticator's ``authenticate_header()``,
    and stock ``SessionAuthentication`` returns ``None`` there (no challenge -> 403). A
    header authenticator returns one, which is why 401 comes back the moment a stack leads
    with an API-key or token authenticator.

    Asserted on the authenticators rather than through a view because DRF binds
    ``APIView.authentication_classes`` at import time, so overriding the setting in a test
    cannot reach an already-imported view.

    Not fixed in this package deliberately: both current consumers lead with a header
    authenticator and already get 401, so there is nothing shared to fix (blueprint §17
    Gate 0). A session-only project that needs 401 subclasses ``SessionAuthentication``
    and overrides ``authenticate_header``. Covered here because this package's own test
    settings used to lead with a JWT authenticator, which hid the whole question.
    """
    from rest_framework.authentication import SessionAuthentication, TokenAuthentication

    assert SessionAuthentication().authenticate_header(None) is None
    assert TokenAuthentication().authenticate_header(None) == "Token"
