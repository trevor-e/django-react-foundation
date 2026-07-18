from django.test import override_settings

from drf_foundation.checks import core_production_messages
from drf_foundation.settings_helpers import production_security_settings

_GOOD_HEADERS = production_security_settings()
_GOOD_SECRET = "x" * 60


@override_settings(DEBUG=False, SECRET_KEY=_GOOD_SECRET, **_GOOD_HEADERS)
def test_clean_config_produces_no_messages():
    assert core_production_messages() == []


@override_settings(DEBUG=True, SECRET_KEY=_GOOD_SECRET, **_GOOD_HEADERS)
def test_debug_flagged_with_prefixed_id():
    (error,) = core_production_messages(id_prefix="myapp")
    assert error.id == "myapp.E001"


@override_settings(DEBUG=False, SECRET_KEY="short", **_GOOD_HEADERS)
def test_weak_secret_key_flagged():
    (error,) = core_production_messages()
    assert error.id == "app.E002"


@override_settings(DEBUG=False, SECRET_KEY=_GOOD_SECRET)
def test_missing_header_block_flagged_with_custom_id():
    # No SECURE_* settings applied at all — the whole block is "absent".
    (error,) = core_production_messages(headers_check_id="legacy.E009")
    assert error.id == "legacy.E009"


def test_env_is_production_markers(monkeypatch):
    from drf_foundation.env import is_production

    monkeypatch.delenv("APP_ENV", raising=False)
    for marker in ("RAILWAY_ENVIRONMENT_NAME", "RAILWAY_PROJECT_ID", "RAILWAY_PUBLIC_DOMAIN"):
        monkeypatch.delenv(marker, raising=False)
    assert is_production() is False

    monkeypatch.setenv("MY_ENV", "production")
    assert is_production("MY_ENV") is True

    monkeypatch.setenv("RAILWAY_PROJECT_ID", "some-id")
    assert is_production() is True
