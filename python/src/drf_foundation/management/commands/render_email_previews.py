"""``render_email_previews`` — write one HTML preview per email kind.

Same committed-artifact + ``--check`` drift-guard pattern as ``export_api_schema``:
regenerate with the plain command, wire ``--check`` into CI so a template edit that
skips regeneration fails loudly instead of silently desyncing the previews your
frontend snapshot suite screenshots.

Default output path: ``settings.EMAIL_PREVIEW_OUTPUT_DIR`` if set, else
``<BASE_DIR>/../frontend/src/emails/previews/`` — the sibling ``frontend/`` layout this
package assumes by default.

A ``palette.json`` is written alongside the HTML so a frontend parity test can compare
the email palette against a live theme without scraping hex values back out of markup.
Fixtures come from :mod:`drf_foundation.email_previews`.
"""

import json
from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from drf_foundation.email_previews import get_previews
from drf_foundation.emails import PALETTE_RADIX_SOURCES, get_theme

PALETTE_FILENAME = "palette.json"


def _default_output_dir() -> Path:
    configured = getattr(settings, "EMAIL_PREVIEW_OUTPUT_DIR", None)
    if configured:
        return Path(configured)
    return Path(settings.BASE_DIR).parent / "frontend" / "src" / "emails" / "previews"


def _palette_json() -> str:
    return (
        json.dumps(
            {"colors": get_theme().palette, "radix_sources": PALETTE_RADIX_SOURCES},
            indent=2,
            sort_keys=True,
        )
        + "\n"
    )


class Command(BaseCommand):
    help = "Render an HTML preview of every email kind."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--output-dir", type=Path, default=None)
        parser.add_argument(
            "--check",
            action="store_true",
            help="Fail (exit 1) if the committed previews are stale; do not write.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        output_dir: Path = options["output_dir"] or _default_output_dir()
        rendered = get_previews().render_all()

        if options["check"]:
            stale = [
                name
                for name, email in rendered.items()
                if not (path := output_dir / f"{name}.html").exists()
                or path.read_text() != email.html
            ]
            palette_path = output_dir / PALETTE_FILENAME
            if not palette_path.exists() or palette_path.read_text() != _palette_json():
                stale.append(PALETTE_FILENAME)
            if stale:
                raise CommandError(
                    f"Email previews are out of date ({', '.join(sorted(stale))}) — a "
                    "template or its copy changed without regenerating. Run "
                    "`render_email_previews` and commit the result."
                )
            self.stdout.write(self.style.SUCCESS("Email previews are up to date."))
            return

        output_dir.mkdir(parents=True, exist_ok=True)
        for name, email in rendered.items():
            (output_dir / f"{name}.html").write_text(email.html)
        (output_dir / PALETTE_FILENAME).write_text(_palette_json())
        self.stdout.write(self.style.SUCCESS(f"Wrote {len(rendered)} previews to {output_dir}"))
