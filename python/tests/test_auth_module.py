"""drf_foundation.auth against Django's *default* username User — proving the
module is user-model-agnostic (projects bring their own email-login model)."""

import pytest
from django.contrib.auth.models import User

from drf_foundation.auth import tokens_for_user


@pytest.fixture
def user(db) -> User:
    return User.objects.create_user(username="pat", password="a-strong-password-123")


def test_login_returns_bare_token_shape(api_client, user):
    response = api_client.post(
        "/api/auth/login", {"username": "pat", "password": "a-strong-password-123"}, format="json"
    )
    assert response.status_code == 200
    assert set(response.json()) == {"access_token", "refresh_token"}


def test_refresh_accepts_refresh_token_key_and_rotates(api_client, user):
    tokens = tokens_for_user(user)

    refreshed = api_client.post(
        "/api/auth/refresh", {"refresh_token": tokens.refresh_token}, format="json"
    )
    assert refreshed.status_code == 200
    assert set(refreshed.json()) == {"access_token", "refresh_token"}

    # Rotation blacklists the old token: replay must fail.
    replay = api_client.post(
        "/api/auth/refresh", {"refresh_token": tokens.refresh_token}, format="json"
    )
    assert replay.status_code == 401


def test_logout_blacklists_the_refresh_token(user, make_authed_client, api_client):
    tokens = tokens_for_user(user)
    client = make_authed_client(user)

    response = client.post(
        "/api/auth/logout", {"refresh_token": tokens.refresh_token}, format="json"
    )
    assert response.status_code == 200
    assert response.json()["data"]["detail"] == "Logged out."

    replay = api_client.post(
        "/api/auth/refresh", {"refresh_token": tokens.refresh_token}, format="json"
    )
    assert replay.status_code == 401


def test_logout_tolerates_garbage_tokens(user, make_authed_client):
    client = make_authed_client(user)
    response = client.post("/api/auth/logout", {"refresh_token": "not-a-jwt"}, format="json")
    assert response.status_code == 200


def test_health_check_is_public(api_client, db):
    response = api_client.get("/api/health")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "ok"
