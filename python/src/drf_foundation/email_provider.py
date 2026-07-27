"""The provider seam behind a project's send choke point: swap the delivery mechanism
without touching the choke point itself.

``DjangoMailProvider`` (the ``EMAIL_BACKEND``-driven dev/test default) needs no mocking
in tests — Django's locmem backend already captures messages. Only ``ResendProvider``'s
call into the ``resend`` SDK is a true external, so that is the one seam a test suite
should stub.

Both providers deliver *multipart* when an HTML body is supplied: the plain-text body
is always the primary content and the HTML rides along as its alternative. Nothing here
ever sends HTML-only — a multipart message delivers better, and the text body is what a
send log should persist.

Select with ``settings.EMAIL_PROVIDER`` (``"django"`` or ``"resend"``, default
``"django"``); ``ResendProvider`` reads ``settings.RESEND_API_KEY``.
"""

from typing import Protocol

from django.conf import settings
from django.core.mail import EmailMultiAlternatives, send_mail


class EmailProvider(Protocol):
    def send(
        self, *, to_email: str, subject: str, body_text: str, body_html: str | None = None
    ) -> None: ...


class DjangoMailProvider:
    """Delivers through whatever ``EMAIL_BACKEND`` is configured — console in dev,
    locmem in tests, SMTP wherever that's the right answer."""

    def send(
        self, *, to_email: str, subject: str, body_text: str, body_html: str | None = None
    ) -> None:
        if body_html is None:
            send_mail(
                subject=subject,
                message=body_text,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[to_email],
            )
            return
        message = EmailMultiAlternatives(
            subject=subject,
            body=body_text,
            from_email=settings.DEFAULT_FROM_EMAIL,
            to=[to_email],
        )
        message.attach_alternative(body_html, "text/html")
        message.send()


class ResendProvider:
    """The `resend` SDK is imported lazily so the package doesn't require it, and so a
    project can keep the import banned outside this module (a ruff ``TID251`` rule is
    the usual way to enforce that the SDK is only reachable through this seam)."""

    def send(
        self, *, to_email: str, subject: str, body_text: str, body_html: str | None = None
    ) -> None:
        import resend

        resend.api_key = settings.RESEND_API_KEY
        params: dict[str, object] = {
            "from": settings.DEFAULT_FROM_EMAIL,
            "to": [to_email],
            "subject": subject,
            "text": body_text,
        }
        if body_html is not None:
            params["html"] = body_html
        resend.Emails.send(params)


def get_provider() -> EmailProvider:
    name = getattr(settings, "EMAIL_PROVIDER", "django")
    if name == "resend":
        return ResendProvider()
    if name == "django":
        return DjangoMailProvider()
    raise ValueError(f"Unknown EMAIL_PROVIDER {name!r} — expected 'django' or 'resend'.")
