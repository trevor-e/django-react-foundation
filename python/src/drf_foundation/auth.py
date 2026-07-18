"""JWT auth wire-contract building blocks (requires the `auth` extra: simplejwt).

The `react-vite-foundation` apiClient speaks one shape: `access_token`/`refresh_token`
keys, `/api/auth/refresh` accepting `{"refresh_token": ...}`, bare (un-enveloped)
token responses. These views/serializers implement that contract against
`get_user_model()`, so projects keep their own User model and registration flow and
import the rest:

```python
# accounts/urls.py
from drf_foundation.auth import LoginView, RefreshView, logout

urlpatterns = [
    path("auth/login", LoginView.as_view(), name="auth-login"),
    path("auth/refresh", RefreshView.as_view(), name="auth-refresh"),
    path("auth/logout", logout, name="auth-logout"),
    ...
]
```

Projects with custom throttles subclass: `class MyLogin(LoginView): throttle_classes = (...)`.
Registration stays project code (every app's signup flow differs — verification
emails, invites, extra fields) built on `tokens_for_user`.
"""

import contextlib

from rest_framework import status as http_status
from rest_framework.decorators import api_view
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from drf_foundation.schemas import Schema, ok, parse
from drf_foundation.throttling import LoginRateThrottle


class AuthTokens(Schema):
    """The apiClient's token shape; auth endpoints return it bare (no envelope —
    the client unwraps `data?.data ?? data`, so both shapes are readable)."""

    access_token: str
    refresh_token: str


class LogoutRequest(Schema):
    refresh_token: str


class LogoutResult(Schema):
    detail: str = "Logged out."


def tokens_for_user(user: object) -> AuthTokens:
    """Mint a fresh access/refresh pair — registration flows return this."""
    refresh = RefreshToken.for_user(user)  # pyrefly: ignore[bad-argument-type]
    return AuthTokens(access_token=str(refresh.access_token), refresh_token=str(refresh))


class AuthTokensObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs: dict[str, object]) -> dict[str, str]:
        result = super().validate(attrs)
        return AuthTokens(
            access_token=result["access"], refresh_token=result["refresh"]
        ).model_dump(mode="json")


class LoginView(TokenObtainPairView):
    permission_classes = (AllowAny,)
    throttle_classes = (LoginRateThrottle,)
    serializer_class = AuthTokensObtainPairSerializer

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        response = super().post(request, *args, **kwargs)
        # simplejwt 200s already; pin it explicitly so subclasses relying on the
        # login contract can't drift.
        response.status_code = http_status.HTTP_200_OK
        return response


class AuthTokensRefreshSerializer(TokenRefreshSerializer):
    def to_internal_value(self, data: object) -> dict[str, object]:
        # The client sends `refresh_token` (matching `AuthTokens`); simplejwt's
        # serializer field is named `refresh`.
        if isinstance(data, dict) and "refresh" not in data and "refresh_token" in data:
            data = {**data, "refresh": data["refresh_token"]}
        return super().to_internal_value(data)

    def validate(self, attrs: dict[str, object]) -> dict[str, str]:
        result = super().validate(attrs)
        return AuthTokens(
            access_token=result["access"],
            refresh_token=str(result.get("refresh", attrs["refresh"])),
        ).model_dump(mode="json")


class RefreshView(TokenRefreshView):
    permission_classes = (AllowAny,)
    serializer_class = AuthTokensRefreshSerializer


def blacklist_refresh_token(encoded: str) -> None:
    """Best-effort blacklist: an invalid/expired token is already unusable, so
    logout never errors on it. Requires simplejwt's token_blacklist app."""
    with contextlib.suppress(TokenError):
        # simplejwt annotates `token` as `Token | None` but accepts the encoded str.
        RefreshToken(encoded).blacklist()  # pyrefly: ignore[bad-argument-type]


@api_view(["POST"])
def logout(request: Request) -> Response:
    data = parse(request, LogoutRequest)
    blacklist_refresh_token(data.refresh_token)
    return ok(LogoutResult())
