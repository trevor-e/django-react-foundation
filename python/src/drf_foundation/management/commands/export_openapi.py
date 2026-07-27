"""``export_openapi`` — write the public API document as OpenAPI 3.1.

Same committed-artifact + ``--check`` drift-guard idiom as ``export_api_schema`` and
``render_email_previews``. Because the document is built by walking the live URLconf,
``--check`` catches three distinct kinds of rot in one step: a new route nobody
documented, a registry entry whose route was renamed or deleted, and a wire-model
change that alters a documented shape.

Default output: ``settings.OPENAPI_OUTPUT`` if set, else ``<BASE_DIR>/../docs/openapi.json``.
"""

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from drf_foundation.openapi import get_spec


def _default_output() -> Path:
    configured = getattr(settings, "OPENAPI_OUTPUT", None)
    if configured:
        return Path(configured)
    return Path(settings.BASE_DIR).parent / "docs" / "openapi.json"


class Command(BaseCommand):
    help = "Export the public API surface as an OpenAPI 3.1 document."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--output", type=Path, default=None)
        parser.add_argument(
            "--check",
            action="store_true",
            help="Fail (exit 1) if the committed document is stale; do not write.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        output: Path = options["output"] or _default_output()
        content = get_spec().dump()

        if options["check"]:
            existing = output.read_text() if output.exists() else None
            if existing != content:
                raise CommandError(
                    f"{output} is out of date — a route or wire model changed without "
                    "regenerating. Run `export_openapi` and commit the result."
                )
            self.stdout.write(self.style.SUCCESS("OpenAPI document is up to date."))
            return

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)
        self.stdout.write(self.style.SUCCESS(f"Wrote {output}"))
