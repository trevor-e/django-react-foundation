"""Project auth surface. Login/refresh/logout come straight from the package
(`drf_foundation.auth` — the apiClient wire contract); this module owns what every
project customizes: registration and the `/api/me` shape."""

from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError as DjangoValidationError
from drf_foundation.auth import tokens_for_user
from drf_foundation.permissions import public_endpoint
from drf_foundation.schemas import err, ok, parse, respond
from drf_foundation.throttling import RegisterRateThrottle
from rest_framework import status as http_status
from rest_framework.decorators import api_view, throttle_classes
from rest_framework.request import Request
from rest_framework.response import Response

from accounts.models import User
from accounts.schemas import Me, RegisterRequest, UpdateMeRequest


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
    return respond(tokens_for_user(user), status=http_status.HTTP_201_CREATED)


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
