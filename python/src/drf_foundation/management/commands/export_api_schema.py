"""``export_api_schema`` — write the API wire contract as JSON Schema.

Default output path: ``settings.WIRE_SCHEMA_OUTPUT`` if set, else
``<BASE_DIR>/../frontend/src/types/api-schema.json`` (i.e. a sibling ``frontend/`` next to
the Django project root — the conventional repo layout this package assumes by default).
``--check``: don't write, just fail if the committed file is stale — wire this into CI as
the schema-drift guard. See ``drf_foundation.wire_schema``.
"""

from pathlib import Path
from typing import Any

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError

from drf_foundation.wire_schema import dump_json_schema


def _default_output() -> Path:
    configured = getattr(settings, "WIRE_SCHEMA_OUTPUT", None)
    if configured:
        return Path(configured)
    base_dir = Path(settings.BASE_DIR)
    return base_dir.parent / "frontend" / "src" / "types" / "api-schema.json"


class Command(BaseCommand):
    help = "Export the Pydantic wire models as a combined JSON Schema for the frontend."

    def add_arguments(self, parser: Any) -> None:
        parser.add_argument("--output", type=Path, default=None)
        parser.add_argument(
            "--check",
            action="store_true",
            help="Fail (exit 1) if the committed file is stale; do not write.",
        )

    def handle(self, *args: Any, **options: Any) -> None:
        output: Path = options["output"] or _default_output()
        content = dump_json_schema()

        if options["check"]:
            existing = output.read_text() if output.exists() else None
            if existing != content:
                raise CommandError(
                    f"{output} is out of date — a wire model changed without "
                    "regenerating. Run your gen-api-types workflow and commit the result."
                )
            self.stdout.write(self.style.SUCCESS(f"{output.name} is up to date."))
            return

        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)
        self.stdout.write(self.style.SUCCESS(f"Wrote {output}"))
