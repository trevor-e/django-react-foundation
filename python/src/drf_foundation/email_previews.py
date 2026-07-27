"""The frozen fixture set every email kind is previewed with.

Three consumers share one registry: the ``render_email_previews`` management command
(which writes committed HTML your frontend snapshot suite can screenshot), a
staff-only preview endpoint if the project exposes one, and the ``--check`` drift
guard in CI.

**Everything a preview renders is a frozen constant on purpose.** No database, no
``date.today()``, no generated ids, and ``FRONTEND_BASE_URL`` pinned during render —
anything varying would make the drift guard flaky, and a flaky guard is worse than no
guard. It would also make an operator preview page useless: "does this look right?"
is unanswerable if the content shifts per request.

Fixtures should deliberately include the awkward cases a sparse set would hide — a
title long enough to wrap the 600px column, an optional field left empty, a list at
its ceiling.

Projects register their own kinds by pointing ``settings.EMAIL_PREVIEWS`` at an
:class:`EmailPreviews` instance::

    # notifications/previews.py
    from drf_foundation.email_previews import EmailPreviews, bundled_renderers
    from notifications.emails import render_digest

    previews = EmailPreviews(
        renderers={**bundled_renderers(), "digest": lambda: render_digest(_TASKS, _UNSUB)},
        base_url="https://example.com",
    )

    # settings.py
    EMAIL_PREVIEWS = "notifications.previews.previews"
"""

from collections.abc import Callable
from dataclasses import dataclass, field

from django.conf import settings
from django.utils.module_loading import import_string

from drf_foundation.emails import (
    Email,
    render_invite,
    render_password_changed,
    render_password_reset,
    render_verification,
)

#: A URL that is obviously not real, so a preview can never be mistaken for live mail
#: and a copy-pasted link can never reach production.
PREVIEW_BASE_URL = "https://example.com"


def bundled_renderers() -> dict[str, Callable[[], Email]]:
    """Frozen fixtures for the kinds this package ships."""
    return {
        "verification": lambda: render_verification(
            f"{PREVIEW_BASE_URL}/verify-email/fake-verification-token"
        ),
        "password_reset": lambda: render_password_reset(
            f"{PREVIEW_BASE_URL}/reset-password/MQ/fake-reset-token", expiry_days=3
        ),
        "password_changed": lambda: render_password_changed(
            changed_at="2026-08-03 09:14 UTC",
            forgot_password_url=f"{PREVIEW_BASE_URL}/forgot-password",  # noqa: S106
        ),
        "invite": lambda: render_invite(
            group_name="The Maple Street House",
            inviter_name="Dana Whitfield",
            invite_url=f"{PREVIEW_BASE_URL}/invite/fake-invite-token",
            blurb="a shared list of everything the group has to stay on top of",
        ),
    }


@dataclass
class EmailPreviews:
    """A named set of zero-argument renderers, rendered with the base URL pinned.

    The layout links its wordmark and CTA at ``settings.FRONTEND_BASE_URL``, which
    differs per environment (localhost in dev, the real host in CI/prod). Pinning it
    while rendering is what makes "are the committed previews up to date?" independent
    of who ran the check.
    """

    renderers: dict[str, Callable[[], Email]] = field(default_factory=bundled_renderers)
    base_url: str = PREVIEW_BASE_URL

    def render_all(self) -> dict[str, Email]:
        original = getattr(settings, "FRONTEND_BASE_URL", None)
        settings.FRONTEND_BASE_URL = self.base_url
        try:
            return {name: render() for name, render in self.renderers.items()}
        finally:
            if original is None:
                delattr(settings, "FRONTEND_BASE_URL")
            else:
                settings.FRONTEND_BASE_URL = original


def get_previews() -> EmailPreviews:
    """The project's registry from ``settings.EMAIL_PREVIEWS`` (a dotted path or an
    :class:`EmailPreviews` instance), else just the bundled kinds."""
    configured = getattr(settings, "EMAIL_PREVIEWS", None)
    if configured is None:
        return EmailPreviews()
    if isinstance(configured, EmailPreviews):
        return configured
    return import_string(configured)
