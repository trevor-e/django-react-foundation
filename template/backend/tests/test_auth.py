"""The auth flow, exercised through the same wire contract the frontend's apiClient uses
in session mode: the credential is an `HttpOnly` cookie, and every session-starting
endpoint hands back the post-rotation CSRF token as a *value* (there is no readable CSRF
cookie under `CSRF_USE_SESSIONS`).
"""


def bootstrap(api_client) -> str:
    """The CSRF token a client fetches before its first unsafe request."""
    response = api_client.get("/api/auth/csrf")
    assert response.status_code == 200
    return response.json()["data"]["csrf_token"]


def test_register_signs_you_in_and_me_works(api_client, db):
    creds = {"email": "new@example.com", "password": "a-strong-password-123"}

    registered = api_client.post(
        "/api/auth/register", creds, format="json", HTTP_X_CSRFTOKEN=bootstrap(api_client)
    )
    assert registered.status_code == 201, registered.content
    # A token value, not a credential: the credential is the cookie the client now holds.
    assert set(registered.json()) == {"csrf_token"}
    assert api_client.cookies["sessionid"]["httponly"]

    # Signing up signs you in, so /api/me works with no follow-up login call.
    me = api_client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["data"]["email"] == creds["email"]


def test_login_then_logout_ends_the_session(api_client, make_user):
    make_user(email="pat@example.com", password="a-strong-password-123")

    logged_in = api_client.post(
        "/api/auth/login",
        {"email": "pat@example.com", "password": "a-strong-password-123"},
        format="json",
        HTTP_X_CSRFTOKEN=bootstrap(api_client),
    )
    assert logged_in.status_code == 200, logged_in.content
    token = logged_in.json()["data"]["csrf_token"]
    assert api_client.get("/api/me").status_code == 200

    assert api_client.post("/api/auth/logout", {}, format="json", HTTP_X_CSRFTOKEN=token).status_code == 200
    assert api_client.get("/api/me").status_code != 200


def test_login_requires_a_csrf_token(make_user):
    """Login CSRF is a real attack — a victim silently signed into the attacker's
    account — and DRF exempts APIViews from the middleware, so the view opts back in.

    Uses its own client because DRF's default test client sets
    `_dont_enforce_csrf_checks`, which makes `csrf_protect` a no-op and would leave this
    assertion vacuous.
    """
    from rest_framework.test import APIClient

    api_client = APIClient(enforce_csrf_checks=True)
    make_user(email="pat@example.com", password="a-strong-password-123")

    response = api_client.post(
        "/api/auth/login",
        {"email": "pat@example.com", "password": "a-strong-password-123"},
        format="json",
    )

    assert response.status_code == 403
    assert "sessionid" not in response.cookies


def test_wrong_password_authenticates_nobody(api_client, make_user):
    make_user(email="pat@example.com", password="a-strong-password-123")

    response = api_client.post(
        "/api/auth/login",
        {"email": "pat@example.com", "password": "wrong-password"},
        format="json",
        HTTP_X_CSRFTOKEN=bootstrap(api_client),
    )

    assert response.status_code == 401
    assert api_client.get("/api/me").status_code != 200


def test_duplicate_email_rejected(api_client, make_user):
    make_user(email="taken@example.com")
    response = api_client.post(
        "/api/auth/register",
        {"email": "taken@example.com", "password": "a-strong-password-123"},
        format="json",
        HTTP_X_CSRFTOKEN=bootstrap(api_client),
    )
    assert response.status_code == 400


def test_me_requires_auth(api_client, db):
    """403 rather than 401: this stack authenticates with the session cookie alone, and
    stock `SessionAuthentication.authenticate_header()` returns None, so DRF has no
    challenge to send. Add a header authenticator first in the list and this becomes 401
    — see the note in config/settings.py."""
    assert api_client.get("/api/me").status_code == 403
