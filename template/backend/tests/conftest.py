import pytest
from rest_framework.test import APIClient
from rest_framework_simplejwt.tokens import RefreshToken

from accounts.models import User


@pytest.fixture
def api_client() -> APIClient:
    return APIClient()


@pytest.fixture
def make_user(db):
    def _make_user(
        email: str = "user@example.com", password: str = "a-strong-password-123", **kwargs: object
    ) -> User:
        return User.objects.create_user(email=email, password=password, **kwargs)

    return _make_user


@pytest.fixture
def make_authed_client():
    """A real-JWT-bearing client (not force_authenticate), matching production auth."""

    def _make(user: User) -> APIClient:
        client = APIClient()
        token = RefreshToken.for_user(user).access_token
        client.credentials(HTTP_AUTHORIZATION=f"Bearer {token}")
        return client

    return _make
