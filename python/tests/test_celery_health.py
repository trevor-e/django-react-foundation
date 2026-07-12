from unittest.mock import MagicMock, patch

import pytest
from django.test import override_settings

from drf_foundation import celery_health


@pytest.fixture(autouse=True)
def _reset_broker_redis_cache():
    celery_health._broker_redis = None
    yield
    celery_health._broker_redis = None


@override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
def test_unreachable_broker_reports_no_workers():
    fake_redis = MagicMock()
    fake_redis.ping.side_effect = ConnectionError("nope")
    with patch("redis.from_url", return_value=fake_redis):
        reachable, workers = celery_health.broker_health(app=MagicMock())
    assert reachable is False
    assert workers == 0


@override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
def test_reachable_broker_counts_workers():
    fake_redis = MagicMock()
    fake_redis.ping.return_value = True
    fake_app = MagicMock()
    fake_app.control.ping.return_value = [{"worker1": "pong"}, {"worker2": "pong"}]
    with patch("redis.from_url", return_value=fake_redis):
        reachable, workers = celery_health.broker_health(fake_app)
    assert reachable is True
    assert workers == 2


@override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
def test_reachable_broker_but_worker_ping_fails_reports_zero_not_unreachable():
    fake_redis = MagicMock()
    fake_redis.ping.return_value = True
    fake_app = MagicMock()
    fake_app.control.ping.side_effect = Exception("boom")
    with patch("redis.from_url", return_value=fake_redis):
        reachable, workers = celery_health.broker_health(fake_app)
    assert reachable is True
    assert workers == 0


@override_settings(CELERY_BROKER_URL="redis://localhost:6379/0")
def test_get_broker_redis_is_cached_across_calls():
    with patch("redis.from_url") as mock_from_url:
        mock_from_url.return_value = MagicMock()
        first = celery_health.get_broker_redis()
        second = celery_health.get_broker_redis()
    assert first is second
    mock_from_url.assert_called_once()
