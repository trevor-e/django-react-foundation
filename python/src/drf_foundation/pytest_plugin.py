"""Pytest fixtures every project's conftest otherwise re-declares.

Auto-loaded via the package's `pytest11` entry point; a project conftest can still
override any fixture by redefining it (conftest wins over plugins) — e.g. a
`make_user` that sets project-specific defaults.
"""

import pytest


@pytest.fixture
def api_client():
    from rest_framework.test import APIClient

    return APIClient()


@pytest.fixture
def make_user(db):
    from django.contrib.auth import get_user_model

    def _make_user(
        email: str = "user@example.com", password: str = "a-strong-password-123", **kwargs: object
    ):
        return get_user_model().objects.create_user(email=email, password=password, **kwargs)

    return _make_user


@pytest.fixture
def make_authed_client():
    """A real-JWT-bearing client, not `force_authenticate` — middleware and
    authenticators that parse the Authorization header themselves (e.g. tenancy
    middleware) never see forced auth, so tests must carry a real token."""
    from rest_framework.test import APIClient
    from rest_framework_simplejwt.tokens import RefreshToken

    def _make(user) -> "APIClient":
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    return _make


@pytest.fixture
def keep_test_connection():
    """For tests that drive the real WSGI/ASGI handler (Schemathesis fuzzing, a raw
    werkzeug client) instead of Django's test client: the real handler's request
    signals run `close_old_connections`, which kills the connection pytest-django's
    atomic wrapper owns ("Cannot open a new connection in an atomic block"). Django's
    own test client disconnects that handler around every request; this fixture does
    the same for the duration of the test.

    Gotcha: nothing in the test may use Django's test client while this is active —
    its handler unconditionally re-connects the signal in a `finally`.
    """
    from django.core.signals import request_finished, request_started
    from django.db import close_old_connections

    request_started.disconnect(close_old_connections)
    request_finished.disconnect(close_old_connections)
    yield
    request_started.connect(close_old_connections)
    request_finished.connect(close_old_connections)
