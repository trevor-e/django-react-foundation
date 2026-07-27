import re

import pytest
from django.test import override_settings

from drf_foundation.emails import (
    DEFAULT_FONT,
    PALETTE_RADIX_SOURCES,
    EmailTheme,
    render,
    render_invite,
    render_password_changed,
    render_password_reset,
    render_verification,
    styles,
)

BRANDED = EmailTheme(wordmark="adulting", wordmark_suffix=".app")

ALL_KINDS = [
    lambda t: render_verification("https://example.com/v/tok", theme=t),
    lambda t: render_password_reset("https://example.com/r/tok", expiry_days=3, theme=t),
    lambda t: render_password_changed("2026-08-03 09:14 UTC", "https://example.com/f", theme=t),
    lambda t: render_invite("The Maple House", "Dana", "https://example.com/i/tok", theme=t),
]


@pytest.mark.parametrize("make", ALL_KINDS)
def test_every_kind_has_both_bodies(make):
    """Text is primary and required; HTML is the alternative. Never HTML-only."""
    email = make(BRANDED)
    assert email.subject
    assert email.text.strip()
    assert email.html.lstrip().startswith("<!DOCTYPE html>")


@pytest.mark.parametrize("make", ALL_KINDS)
def test_no_remote_assets_anywhere(make):
    """No web font, no external image, nothing that could become a tracking pixel."""
    html = make(BRANDED).html
    assert "<img" not in html
    assert "<style" not in html
    assert "@import" not in html
    assert "<link" not in html


@pytest.mark.parametrize("make", ALL_KINDS)
def test_layout_is_tables_with_inline_styles(make):
    html = make(BRANDED).html
    assert 'role="presentation"' in html
    assert 'style="' in html


def test_brand_reaches_subject_and_body():
    email = render_password_changed("now", "https://example.com/f", theme=BRANDED)
    assert "adulting.app" in email.subject
    assert "adulting.app" in email.text
    assert "adulting" in email.html


def test_user_controlled_text_is_escaped_not_interpolated():
    """Group and inviter names are user-controlled and land in HTML. There is no
    |safe / mark_safe anywhere in this module, and this is the test that holds it."""
    email = render_invite(
        group_name="<script>alert(1)</script>",
        inviter_name='Dana " onmouseover="x',
        invite_url="https://example.com/i/tok",
        theme=BRANDED,
    )
    assert "<script>alert(1)</script>" not in email.html
    assert "&lt;script&gt;" in email.html
    assert ' onmouseover="x' not in email.html


def test_unsubscribe_link_absent_unless_supplied():
    """A password-reset email is not opt-outable; offering an unsubscribe link would
    imply otherwise."""
    assert "Unsubscribe" not in render_password_reset("https://e.com/r", 3, theme=BRANDED).html


def test_unsubscribe_link_renders_when_supplied():
    html = render(
        "drf_foundation/email/verification.html",
        "s",
        {
            "verify_url": "https://e.com/v",
            "unsubscribe_url": "https://e.com/u",
            "unsubscribe_label": "Unsubscribe from digests",
        },
        theme=BRANDED,
    )
    assert "Unsubscribe from digests" in html
    assert "https://e.com/u" in html


def test_palette_override_reaches_the_markup():
    """Rebranding is a palette swap, not a template fork."""
    theme = BRANDED.with_palette(accent="#AA0000")
    assert "#AA0000" in render_verification("https://e.com/v", theme=theme).html


def test_with_palette_does_not_mutate_the_original():
    BRANDED.with_palette(accent="#AA0000")
    assert BRANDED.palette["accent"] == "#3E63DD"


def test_font_stack_carries_no_quotes():
    """Quoted family names would be escaped by autoescaping and corrupt every style
    attribute they appear in."""
    assert '"' not in DEFAULT_FONT
    assert "'" not in DEFAULT_FONT
    assert "Segoe UI" in DEFAULT_FONT


def test_every_radix_mapped_key_exists_in_the_palette():
    """The mapping is what a frontend parity test drives; a key that isn't in the
    palette would make that test silently vacuous."""
    palette = EmailTheme().palette
    assert set(PALETTE_RADIX_SOURCES) <= set(palette)


def test_styles_reference_only_palette_colors():
    """Every hex in the rendered styles must come from the palette — a literal typed
    into a style string is exactly the drift the palette exists to prevent."""
    theme = EmailTheme()
    allowed = {value.upper() for value in theme.palette.values()}
    found = {m.upper() for m in re.findall(r"#[0-9A-Fa-f]{6}", " ".join(styles(theme).values()))}
    assert found <= allowed


@override_settings(EMAIL_THEME=BRANDED)
def test_theme_is_read_from_settings_when_not_passed():
    assert "adulting" in render_verification("https://e.com/v").html


def test_default_footer_reason_names_the_brand():
    assert BRANDED.resolved_footer_reason() == (
        "You're receiving this because you have an account with adulting.app."
    )


def test_footer_reason_can_be_overridden():
    theme = EmailTheme(wordmark="X", footer_reason="Custom reason.")
    assert "Custom reason." in render_verification("https://e.com/v", theme=theme).html
