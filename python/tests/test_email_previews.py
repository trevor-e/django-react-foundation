import json

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings

from drf_foundation.email_previews import EmailPreviews, bundled_renderers, get_previews
from drf_foundation.emails import Email, EmailTheme

BRANDED = EmailTheme(wordmark="adulting", wordmark_suffix=".app")


def test_bundled_kinds_all_render():
    rendered = EmailPreviews().render_all()
    assert set(rendered) == {"verification", "password_reset", "password_changed", "invite"}
    assert all(isinstance(email, Email) for email in rendered.values())


def test_render_is_deterministic():
    """The drift guard is only meaningful if two renders are byte-identical."""
    first = EmailPreviews().render_all()
    second = EmailPreviews().render_all()
    assert {k: v.html for k, v in first.items()} == {k: v.html for k, v in second.items()}


def test_base_url_is_pinned_during_render():
    """Otherwise "are the previews up to date?" depends on who ran the check."""
    rendered = EmailPreviews(base_url="https://pinned.example").render_all()
    assert "https://pinned.example" in rendered["verification"].html


def test_base_url_is_restored_afterwards():
    from django.conf import settings

    before = settings.FRONTEND_BASE_URL
    EmailPreviews(base_url="https://pinned.example").render_all()
    assert before == settings.FRONTEND_BASE_URL


def test_projects_can_add_their_own_kinds():
    previews = EmailPreviews(
        renderers={
            **bundled_renderers(),
            "digest": lambda: Email(subject="d", text="d", html="<html>d</html>"),
        }
    )
    assert "digest" in previews.render_all()


@override_settings(EMAIL_PREVIEWS=EmailPreviews(renderers={}))
def test_registry_instance_from_settings():
    assert get_previews().render_all() == {}


def test_default_registry_when_unset():
    assert set(get_previews().render_all()) == set(bundled_renderers())


@pytest.fixture
def previews_dir(tmp_path):
    with override_settings(EMAIL_THEME=BRANDED):
        yield tmp_path


def test_command_writes_one_file_per_kind_plus_palette(previews_dir):
    call_command("render_email_previews", "--output-dir", str(previews_dir))
    written = {p.name for p in previews_dir.iterdir()}
    assert "verification.html" in written
    assert "palette.json" in written


def test_palette_json_carries_colors_and_radix_sources(previews_dir):
    call_command("render_email_previews", "--output-dir", str(previews_dir))
    payload = json.loads((previews_dir / "palette.json").read_text())
    assert payload["colors"]["accent"] == BRANDED.palette["accent"]
    assert payload["radix_sources"]["accent"] == "accent-9"


def test_check_passes_on_freshly_written_previews(previews_dir):
    call_command("render_email_previews", "--output-dir", str(previews_dir))
    call_command("render_email_previews", "--output-dir", str(previews_dir), "--check")


def test_check_fails_when_a_template_edit_was_not_regenerated(previews_dir):
    call_command("render_email_previews", "--output-dir", str(previews_dir))
    (previews_dir / "verification.html").write_text("<html>stale</html>")
    with pytest.raises(CommandError, match="verification"):
        call_command("render_email_previews", "--output-dir", str(previews_dir), "--check")


def test_check_fails_when_the_palette_drifts(previews_dir):
    """A palette change with no template change still has to fail the guard."""
    call_command("render_email_previews", "--output-dir", str(previews_dir))
    with (
        override_settings(EMAIL_THEME=BRANDED.with_palette(accent="#AA0000")),
        pytest.raises(CommandError, match="palette.json"),
    ):
        call_command("render_email_previews", "--output-dir", str(previews_dir), "--check")


def test_check_fails_when_previews_were_never_generated(tmp_path):
    with pytest.raises(CommandError, match="out of date"):
        call_command("render_email_previews", "--output-dir", str(tmp_path), "--check")


def test_check_does_not_write(tmp_path):
    with pytest.raises(CommandError):
        call_command("render_email_previews", "--output-dir", str(tmp_path), "--check")
    assert list(tmp_path.iterdir()) == []
