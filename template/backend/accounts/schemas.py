from django.core.exceptions import ValidationError as DjangoValidationError
from django.core.validators import validate_email
from drf_foundation.schemas import Schema
from pydantic import field_validator


class RegisterRequest(Schema):
    email: str
    password: str

    @field_validator("email")
    @classmethod
    def validate_email_format(cls, value: str) -> str:
        try:
            validate_email(value)
        except DjangoValidationError as exc:
            raise ValueError("Enter a valid email address.") from exc
        return value


class Me(Schema):
    id: int
    email: str
    name: str


class UpdateMeRequest(Schema):
    name: str | None = None
