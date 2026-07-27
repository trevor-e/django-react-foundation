import pytest

from drf_foundation.crons import CronJob, CronRegistry

REGISTRY = CronRegistry(
    {
        "hourly-sweep": CronJob(task="app.tasks.sweep", minute="20", max_runtime=5),
        "nightly-rollup": CronJob(task="app.tasks.rollup", minute="0", hour="7"),
    }
)


def test_beat_schedule_names_match_registry_keys():
    """The key is also the Sentry monitor slug — a rename orphans monitor history."""
    schedule = REGISTRY.beat_schedule()
    assert set(schedule) == {"hourly-sweep", "nightly-rollup"}
    assert schedule["hourly-sweep"]["task"] == "app.tasks.sweep"


def test_beat_and_sentry_read_the_same_fields():
    """The whole point of the registry: one declaration, two consumers, no drift."""
    job = REGISTRY.jobs["nightly-rollup"]
    celery = job.celery_schedule()
    assert celery.hour == {7}
    assert celery.minute == {0}
    assert job.monitor_config()["schedule"]["value"] == "0 7 * * *"


def test_monitor_config_carries_thresholds_the_sdk_would_have_omitted():
    """The SDK emits only schedule/timezone, so monitors inherit Sentry's defaults —
    where one late check-in is already an outage. These fields are the fix."""
    config = REGISTRY.jobs["hourly-sweep"].monitor_config()
    assert config["max_runtime"] == 5
    assert config["checkin_margin"] == 5
    assert config["failure_issue_threshold"] == 2
    assert config["recovery_threshold"] == 1


def test_timezone_is_shared_by_both_renderings():
    registry = CronRegistry({"j": CronJob(task="t", minute="0")}, timezone="America/New_York")
    assert registry.jobs["j"].monitor_config(registry.timezone)["timezone"] == "America/New_York"


def test_unknown_slug_raises_rather_than_going_unmonitored():
    with pytest.raises(KeyError, match="No cron registry entry for 'typo'"):
        REGISTRY.monitor("typo")


def test_monitor_returns_a_passthrough_decorator_without_sentry_configured():
    """Dev/test/CI have no Sentry client; the decorator must not change behavior."""

    @REGISTRY.monitor("hourly-sweep")
    def work(x):
        return x * 2

    assert work(21) == 42


def test_monitor_lets_exceptions_propagate():
    @REGISTRY.monitor("hourly-sweep")
    def boom():
        raise ValueError("kaboom")

    with pytest.raises(ValueError, match="kaboom"):
        boom()
