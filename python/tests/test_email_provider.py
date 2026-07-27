import pytest
from django.core import mail
from django.test import override_settings

from drf_foundation.email_provider import DjangoMailProvider, ResendProvider, get_provider


def test_text_only_send_is_a_plain_message():
    DjangoMailProvider().send(to_email="a@b.c", subject="Hi", body_text="hello")
    assert len(mail.outbox) == 1
    assert mail.outbox[0].body == "hello"
    assert mail.outbox[0].alternatives == []


def test_html_send_is_multipart_with_text_primary():
    """Never HTML-only: a multipart message delivers better, and the text body is what
    a send log persists."""
    DjangoMailProvider().send(
        to_email="a@b.c", subject="Hi", body_text="hello", body_html="<p>hello</p>"
    )
    message = mail.outbox[0]
    assert message.body == "hello"
    assert message.alternatives == [("<p>hello</p>", "text/html")]


def test_recipient_and_subject_are_passed_through():
    DjangoMailProvider().send(to_email="a@b.c", subject="Subj", body_text="x")
    assert mail.outbox[0].to == ["a@b.c"]
    assert mail.outbox[0].subject == "Subj"


def test_default_provider_is_django():
    assert isinstance(get_provider(), DjangoMailProvider)


@override_settings(EMAIL_PROVIDER="resend")
def test_resend_selected_by_setting():
    assert isinstance(get_provider(), ResendProvider)


@override_settings(EMAIL_PROVIDER="carrier-pigeon")
def test_unknown_provider_fails_loudly():
    """Silently falling back would mean prod mail quietly going to the console."""
    with pytest.raises(ValueError, match="carrier-pigeon"):
        get_provider()


@override_settings(RESEND_API_KEY="test-key", DEFAULT_FROM_EMAIL="noreply@example.com")
def test_resend_payload_shape(monkeypatch):
    """The SDK call is the one true external, so this is the seam worth stubbing."""
    sent = {}

    class FakeEmails:
        @staticmethod
        def send(params):
            sent.update(params)

    fake = type("resend", (), {"api_key": None, "Emails": FakeEmails})
    monkeypatch.setitem(__import__("sys").modules, "resend", fake)

    ResendProvider().send(to_email="a@b.c", subject="Subj", body_text="text", body_html="<p>h</p>")
    assert sent["to"] == ["a@b.c"]
    assert sent["text"] == "text"
    assert sent["html"] == "<p>h</p>"
    assert sent["from"] == "noreply@example.com"


@override_settings(RESEND_API_KEY="test-key")
def test_resend_omits_html_when_absent(monkeypatch):
    sent = {}

    class FakeEmails:
        @staticmethod
        def send(params):
            sent.update(params)

    fake = type("resend", (), {"api_key": None, "Emails": FakeEmails})
    monkeypatch.setitem(__import__("sys").modules, "resend", fake)

    ResendProvider().send(to_email="a@b.c", subject="S", body_text="text")
    assert "html" not in sent
