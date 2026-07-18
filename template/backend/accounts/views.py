import contextlib

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_foundation.permissions import public_endpoint
from drf_foundation.schemas import err, ok, parse, respond
from drf_foundation.throttling import LoginRateThrottle, RegisterRateThrottle
from rest_framework import status as http_status
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.permissions import AllowAny
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework_simplejwt.exceptions import TokenError
from rest_framework_simplejwt.serializers import TokenObtainPairSerializer, TokenRefreshSerializer
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView

from accounts.models import User
from accounts.schemas import (
    AuthTokens,
    LogoutRequest,
    LogoutResult,
    Me,
    RegisterRequest,
    UpdateMeRequest,
)


def _tokens_for_user(user: User) -> AuthTokens:
    refresh = RefreshToken.for_user(user)
    return AuthTokens(access_token=str(refresh.access_token), refresh_token=str(refresh))


@api_view(["POST"])
@public_endpoint
@throttle_classes([RegisterRateThrottle])
def register(request: Request) -> Response:
    data = parse(request, RegisterRequest)
    if User.objects.filter(email__iexact=data.email).exists():
        return err(
            "An account with this email already exists.",
            status=http_status.HTTP_400_BAD_REQUEST,
        )
    try:
        validate_password(data.password)
    except DjangoValidationError as exc:
        return err(" ".join(exc.messages), status=http_status.HTTP_400_BAD_REQUEST)
    user = User.objects.create_user(email=data.email, password=data.password)
    return respond(_tokens_for_user(user), status=http_status.HTTP_201_CREATED)


class _AuthTokensObtainPairSerializer(TokenObtainPairSerializer):
    def validate(self, attrs: dict[str, object]) -> dict[str, str]:
        result = super().validate(attrs)
        return AuthTokens(
            access_token=result["access"], refresh_token=result["refresh"]
        ).model_dump(mode="json")


class LoginView(TokenObtainPairView):
    permission_classes = (AllowAny,)
    throttle_classes = (LoginRateThrottle,)
    serializer_class = _AuthTokensObtainPairSerializer

    def post(self, request: Request, *args: object, **kwargs: object) -> Response:
        response = super().post(request, *args, **kwargs)
        response.status_code = http_status.HTTP_200_OK
        return response


class _AuthTokensRefreshSerializer(TokenRefreshSerializer):
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
    serializer_class = _AuthTokensRefreshSerializer


@api_view(["POST"])
def logout(request: Request) -> Response:
    data = parse(request, LogoutRequest)
    with contextlib.suppress(TokenError):
        # simplejwt annotates `token` as `Token | None` but accepts the encoded str.
        RefreshToken(data.refresh_token).blacklist()  # pyrefly: ignore[bad-argument-type]
    return ok(LogoutResult(detail="Logged out."))


@api_view(["GET", "PATCH"])
def me(request: Request) -> Response:
    user = request.user
    assert isinstance(user, User)
    if request.method == "PATCH":
        data = parse(request, UpdateMeRequest)
        if data.name is not None:
            user.name = data.name
            user.save(update_fields=["name"])
    return ok(Me(id=user.id, email=user.email, name=user.name))
