import tomllib

from drf_foundation.fuzz import DISABLED_CHECKS, fuzz_config_text


def parsed(**kwargs) -> dict:
    return tomllib.loads(fuzz_config_text(**kwargs))


def test_max_examples_defaults_and_is_overridable():
    assert parsed()["generation"]["max-examples"] == 10
    assert parsed(max_examples=100)["generation"]["max-examples"] == 100


def test_max_examples_reads_the_env_var(monkeypatch):
    """A deeper hunt should be an env var, not an edit."""
    monkeypatch.setenv("FUZZ_MAX_EXAMPLES", "250")
    assert parsed()["generation"]["max-examples"] == 250


def test_security_parameters_are_not_generated():
    """Every request should carry the harness's real credential; generating junk
    tokens just produces a run of 401s."""
    assert parsed()["generation"]["with-security-parameters"] is False


def test_shrinking_is_off():
    """Each shrink step replays real requests; a few failures become tens of minutes."""
    assert parsed()["generation"]["no-shrink"] is True


def test_both_generation_modes_run():
    assert parsed()["generation"]["mode"] == "all"


def test_every_documented_disabled_check_is_actually_disabled():
    config = parsed()
    for name in DISABLED_CHECKS:
        assert config["checks"][name]["enabled"] is False


def test_the_valuable_checks_are_not_disabled():
    """server_error and response_conformance are the whole point — a config that
    silenced them would run green and prove nothing."""
    assert "server_error" not in DISABLED_CHECKS
    assert "response_conformance" not in DISABLED_CHECKS
    assert "server_error" not in parsed().get("checks", {})


def test_each_disabled_check_carries_a_reason_in_the_emitted_config():
    """The rationale travels with the config rather than rotting in a commit message."""
    text = fuzz_config_text()
    assert all(reason.split(";")[0][:30] in text for reason in DISABLED_CHECKS.values())


def test_disabled_checks_are_overridable():
    config = parsed(disabled_checks={"only_this": "because"})
    assert set(config["checks"]) == {"only_this"}


def test_emitted_config_is_valid_toml():
    assert isinstance(parsed(), dict)
