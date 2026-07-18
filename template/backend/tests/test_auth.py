"""The auth flow, exercised through the same wire contract the frontend's apiClient
uses: auth endpoints return the BARE token shape (drf_foundation's `respond`, no
envelope — the apiClient unwraps `data?.data ?? data` so both work), and
/api/auth/refresh accepts `refresh_token`."""


def test_register_login_refresh_me_roundtrip(api_client, db):
    creds = {"email": "new@example.com", "password": "a-strong-password-123"}

    registered = api_client.post("/api/auth/register", creds, format="json")
    assert registered.status_code == 201
    assert set(registered.json()) == {"access_token", "refresh_token"}

    logged_in = api_client.post("/api/auth/login", creds, format="json")
    assert logged_in.status_code == 200
    tokens = logged_in.json()

    refreshed = api_client.post(
        "/api/auth/refresh", {"refresh_token": tokens["refresh_token"]}, format="json"
    )
    assert refreshed.status_code == 200
    new_tokens = refreshed.json()
    assert set(new_tokens) == {"access_token", "refresh_token"}

    api_client.credentials(HTTP_AUTHORIZATION=f"Bearer {new_tokens['access_token']}")
    me = api_client.get("/api/me")
    assert me.status_code == 200
    assert me.json()["data"]["email"] == creds["email"]


def test_duplicate_email_rejected(api_client, make_user):
    make_user(email="taken@example.com")
    response = api_client.post(
        "/api/auth/register",
        {"email": "taken@example.com", "password": "a-strong-password-123"},
        format="json",
    )
    assert response.status_code == 400


def test_me_requires_auth(api_client, db):
    assert api_client.get("/api/me").status_code == 401


def test_rotated_refresh_token_is_blacklisted(api_client, db):
    creds = {"email": "rotate@example.com", "password": "a-strong-password-123"}
    tokens = api_client.post("/api/auth/register", creds, format="json").json()

    first = api_client.post(
        "/api/auth/refresh", {"refresh_token": tokens["refresh_token"]}, format="json"
    )
    assert first.status_code == 200

    replay = api_client.post(
        "/api/auth/refresh", {"refresh_token": tokens["refresh_token"]}, format="json"
    )
    assert replay.status_code == 401
