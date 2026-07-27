"""HTML email bodies: one plain-text body and one HTML body per kind.

Promoted from adulting.app. The package already owns the *flows* that send
transactional mail (``drf_foundation.auth`` issues verification and password-reset
tokens; ``/auth-ui`` renders their pages) but shipped no bodies for the mail those
flows send — every project re-derived the same table-layout markup by hand.

Three rules hold across every template here, and they are the reason this is a shared
layer rather than a snippet to copy:

1. **Inline styles, table layout, literal hex.** Email clients have no CSS custom
   properties, no reliable ``<style>`` block, and no external stylesheets. The
   :class:`EmailTheme` palette is interpolated into ``style=""`` attributes so a color
   lives in exactly one place. The font stack deliberately carries no quoted family
   names: CSS allows a multi-identifier family (``Segoe UI``) unquoted, and staying
   quote-free keeps every style string untouched by autoescaping.
2. **Autoescaping is never disabled.** Display names, group names, and any other
   user-controlled string land in HTML here. There is no ``|safe``, no ``mark_safe``,
   and no ``format_html`` in this module or its templates. Keep it that way.
3. **No remote assets.** No web font, no tracking pixel, no external image. A logo
   would need a remote fetch, which is a read receipt whether or not you meant it as
   one — so the brand is a text wordmark.

Every renderer is a pure function of its arguments — no database, no ``date.today()``
— so :mod:`drf_foundation.email_previews` can regenerate byte-identical previews
without a database, and the drift guard watching those previews can't go flaky.

Senders hand both bodies to whatever send choke point the project owns; keep the
message multipart (text primary, HTML alternative) rather than HTML-only.
"""

from dataclasses import dataclass, field, replace

from django.conf import settings
from django.template.loader import render_to_string

#: Palette key -> the Radix Themes CSS variable it is a copy of, for the entries a
#: parity test should enforce. Email can't read CSS custom properties, so these hex
#: values are necessarily a *copy* of the app's theme and can never be live-linked to
#: it. This mapping is what keeps the copy honest: a frontend test can read the real
#: variables out of a mounted ``<Theme>`` and fail when one drifts. Entries deliberately
#: not tied to the theme (pure white, semantic warning colors) are absent.
PALETTE_RADIX_SOURCES = {
    "page_bg": "gray-3",
    "border": "gray-6",
    "text": "gray-12",
    "muted": "gray-11",
    "accent": "accent-9",
    "link": "accent-11",
}

#: No quoted family names — see rule 1.
DEFAULT_FONT = "-apple-system, BlinkMacSystemFont, Segoe UI, Roboto, Helvetica, Arial, sans-serif"


def _default_palette() -> dict[str, str]:
    return {
        "page_bg": "#F0F0F3",
        # A card is plain white rather than gray-1 (#fcfcfd) — against the gray-3 page
        # it reads as paper, and the extra contrast survives clients that wash out.
        "surface": "#FFFFFF",
        "border": "#D9D9E0",
        "text": "#1C2024",
        "muted": "#60646C",
        "accent": "#3E63DD",
        # Text on the accent button — white by construction, not a scale step.
        "accent_text": "#FFFFFF",
        "link": "#3A5BC7",
        # Semantic warning, not brand: these stay put when the accent changes.
        "alert_bg": "#FFF8F0",
        "alert_border": "#FFDCC3",
    }


@dataclass(frozen=True)
class EmailTheme:
    """Brand and palette for the shared email shell.

    ``wordmark``/``wordmark_suffix`` render as one text logo with the suffix tinted in
    the accent color (``adulting`` + ``.app``). Pass an empty suffix for a single-word
    brand. ``footer_reason`` is the "why am I getting this" line every compliant
    transactional email carries.
    """

    wordmark: str = "Your app"
    wordmark_suffix: str = ""
    footer_reason: str = ""
    font: str = DEFAULT_FONT
    palette: dict[str, str] = field(default_factory=_default_palette)

    def with_palette(self, **overrides: str) -> "EmailTheme":
        """A copy with individual palette entries replaced — the usual way to rebrand
        is ``theme.with_palette(accent="#...", link="#...")``."""
        return replace(self, palette={**self.palette, **overrides})

    def resolved_footer_reason(self) -> str:
        if self.footer_reason:
            return self.footer_reason
        # Phrased to avoid an article before the brand — a generic default can't know
        # whether a given wordmark takes "a" or "an".
        return f"You're receiving this because you have an account with {self.brand_label()}."

    def brand_label(self) -> str:
        return f"{self.wordmark}{self.wordmark_suffix}"


def styles(theme: "EmailTheme") -> dict[str, str]:
    """Every inline style string the templates use, derived from one palette."""
    p = theme.palette
    font = theme.font
    return {
        "body": (
            f"margin:0; padding:0; background-color:{p['page_bg']}; -webkit-text-size-adjust:100%;"
        ),
        "page": f"background-color:{p['page_bg']};",
        "frame": "max-width:600px; margin:0 auto;",
        "wordmark": (
            f"font-family:{font}; font-size:16px; font-weight:600; "
            f"color:{p['text']}; text-decoration:none;"
        ),
        "wordmark_accent": f"color:{p['accent']};",
        "card": (
            f"background-color:{p['surface']}; border:1px solid {p['border']}; "
            f"border-radius:12px; padding:32px 28px;"
        ),
        "h1": (
            f"margin:0 0 16px 0; font-family:{font}; font-size:22px; line-height:1.3; "
            f"font-weight:600; color:{p['text']};"
        ),
        "p": (
            f"margin:0 0 16px 0; font-family:{font}; font-size:15px; line-height:1.6; "
            f"color:{p['text']};"
        ),
        "p_last": (
            f"margin:0; font-family:{font}; font-size:15px; line-height:1.6; color:{p['text']};"
        ),
        # For a paragraph directly after a button. The button carries only a small
        # cushion, so whatever follows owns the gap — that way a trailing button doesn't
        # stack its own bottom margin on top of the card's padding.
        "p_after_cta": (
            f"margin:16px 0 0 0; font-family:{font}; font-size:15px; line-height:1.6; "
            f"color:{p['text']};"
        ),
        "small": (
            f"margin:16px 0 0 0; font-family:{font}; font-size:13px; line-height:1.6; "
            f"color:{p['muted']};"
        ),
        "link": f"color:{p['link']}; text-decoration:underline;",
        "button_cell": f"background-color:{p['accent']}; border-radius:8px;",
        "button_link": (
            f"display:inline-block; padding:12px 24px; font-family:{font}; font-size:15px; "
            f"font-weight:600; color:{p['accent_text']}; text-decoration:none;"
        ),
        # Vertical spacing lives on `alert_table` (a <table>), never on the cell: CSS
        # margins do not apply to <td>, so a margin there is silently dropped and the
        # callout sits flush against its neighbours.
        "alert_table": "margin:8px 0 20px 0;",
        "alert": (
            f"background-color:{p['alert_bg']}; border:1px solid {p['alert_border']}; "
            f"border-radius:8px; padding:16px 18px;"
        ),
        "footer": (
            f"margin:0 0 6px 0; font-family:{font}; font-size:12px; line-height:1.6; "
            f"color:{p['muted']};"
        ),
        "footer_link": f"color:{p['muted']}; text-decoration:underline;",
    }


@dataclass(frozen=True)
class Email:
    """A rendered message. ``text`` is the primary body and is what a send log should
    persist; ``html`` rides along as the alternative."""

    subject: str
    text: str
    html: str


def get_theme() -> EmailTheme:
    """The project's theme, from ``settings.EMAIL_THEME``, else the neutral default."""
    return getattr(settings, "EMAIL_THEME", None) or EmailTheme()


def app_url() -> str:
    """Where the wordmark and CTAs point. ``FRONTEND_BASE_URL`` is the stack's
    convention (see the blueprint's local/prod switch)."""
    return getattr(settings, "FRONTEND_BASE_URL", "")


def render(
    template: str,
    subject: str,
    context: dict | None = None,
    *,
    theme: EmailTheme | None = None,
) -> str:
    """Render one body template inside the shared shell.

    ``template`` is a template path — pass ``"drf_foundation/email/verification.html"``
    for a bundled kind, or your own template extending
    ``"drf_foundation/email/layout.html"`` for a project-specific one.
    """
    resolved = theme or get_theme()
    return render_to_string(
        template,
        {
            "subject": subject,
            "palette": resolved.palette,
            "s": styles(resolved),
            "theme": resolved,
            "app_url": app_url(),
            "brand": resolved.brand_label(),
            "footer_reason": resolved.resolved_footer_reason(),
            **(context or {}),
        },
    )


def render_verification(verify_url: str, *, theme: EmailTheme | None = None) -> Email:
    brand = (theme or get_theme()).brand_label()
    subject = "Verify your email address"
    return Email(
        subject=subject,
        text=(
            f"Welcome to {brand}!\n\n"
            "Confirm this is your email address by opening the link below.\n\n"
            f"{verify_url}\n\n"
            "If you didn't create this account, you can ignore this email."
        ),
        html=render(
            "drf_foundation/email/verification.html",
            subject,
            {"verify_url": verify_url},
            theme=theme,
        ),
    )


def render_password_reset(
    reset_url: str, expiry_days: int, *, theme: EmailTheme | None = None
) -> Email:
    subject = "Reset your password"
    return Email(
        subject=subject,
        text=(
            "Someone requested a password reset for this account.\n\n"
            f"Reset your password: {reset_url}\n\n"
            f"This link works once and expires in {expiry_days} days. "
            "If you didn't request this, you can ignore this email — your "
            "password won't change."
        ),
        html=render(
            "drf_foundation/email/password_reset.html",
            subject,
            {"reset_url": reset_url, "expiry_days": expiry_days},
            theme=theme,
        ),
    )


def render_password_changed(
    changed_at: str, forgot_password_url: str, *, theme: EmailTheme | None = None
) -> Email:
    brand = (theme or get_theme()).brand_label()
    subject = f"Your {brand} password was changed"
    return Email(
        subject=subject,
        text=(
            f"The password for your {brand} account was changed on {changed_at}.\n\n"
            "If this was you, you're all set — no action needed.\n\n"
            "If this wasn't you, someone else may have access to your account. "
            "Reset your password immediately:\n\n"
            f"{forgot_password_url}\n\n"
            "Then reply to this email to reach support."
        ),
        html=render(
            "drf_foundation/email/password_changed.html",
            subject,
            {"changed_at": changed_at, "forgot_password_url": forgot_password_url},
            theme=theme,
        ),
    )


def render_invite(
    group_name: str,
    inviter_name: str,
    invite_url: str,
    *,
    blurb: str = "",
    theme: EmailTheme | None = None,
) -> Email:
    """An invitation to join a shared group — whatever the project calls its tenant
    (household, workspace, team, organization). ``blurb`` is an optional sentence
    describing what the recipient is being invited into."""
    brand = (theme or get_theme()).brand_label()
    subject = f"You're invited to join {group_name} on {brand}"
    return Email(
        subject=subject,
        text=(
            f"{inviter_name} invited you to join {group_name} on {brand}."
            f"{' ' + blurb if blurb else ''}\n\nAccept your invite: {invite_url}"
        ),
        html=render(
            "drf_foundation/email/invite.html",
            subject,
            {
                "group_name": group_name,
                "inviter_name": inviter_name,
                "invite_url": invite_url,
                "blurb": blurb,
            },
            theme=theme,
        ),
    )
