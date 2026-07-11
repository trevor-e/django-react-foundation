from pathlib import Path

import pytest
from django.core.management import call_command
from django.core.management.base import CommandError

from drf_foundation.wire_schema import dump_json_schema


def test_writes_schema_to_explicit_output(tmp_path: Path):
    output = tmp_path / "api-schema.json"
    call_command("export_api_schema", f"--output={output}")
    assert output.read_text() == dump_json_schema()


def test_check_passes_when_up_to_date(tmp_path: Path):
    output = tmp_path / "api-schema.json"
    output.write_text(dump_json_schema())
    call_command("export_api_schema", f"--output={output}", "--check")


def test_check_fails_when_stale(tmp_path: Path):
    output = tmp_path / "api-schema.json"
    output.write_text('{"stale": true}\n')
    with pytest.raises(CommandError):
        call_command("export_api_schema", f"--output={output}", "--check")


def test_check_fails_when_missing(tmp_path: Path):
    output = tmp_path / "does-not-exist.json"
    with pytest.raises(CommandError):
        call_command("export_api_schema", f"--output={output}", "--check")
