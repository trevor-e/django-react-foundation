"""Scheduled-job registry: one declaration per beat job, feeding both Celery's beat
schedule and that job's Sentry cron monitor.

**Why this exists rather than** ``CeleryIntegration(monitor_beat_tasks=True)``.
The SDK's beat instrumentation splits a single check-in across two processes: the
``in_progress`` check-in is captured in the *beat* process, and the terminal check-in
in the *worker*, with the duration computed as the worker's ``time.time()`` minus a
timestamp header stamped back in beat. Any clock skew or queue latency between those
processes lands in the reported duration, so tasks that finish in milliseconds report
as minutes long. Worse, ``_get_monitor_config`` emits only ``schedule``/``timezone``,
so the auto-created monitors inherit Sentry's defaults — where a *single* late
check-in is already an outage issue. The observed result (adulting.app, 2026-07-27)
was three monitors red with nothing actually broken.

So :meth:`CronRegistry.monitor` wraps the task body instead. Both check-ins happen in
the worker off one monotonic clock, and every job declares its own runtime and failure
thresholds here. Registering a job is a single entry — :meth:`beat_schedule` and the
monitor config both read from it, so the schedule Celery runs and the schedule Sentry
checks against cannot drift apart.

Usage::

    # config/crons.py
    from drf_foundation.crons import CronJob, CronRegistry

    registry = CronRegistry({
        "send-weekly-digests": CronJob(task="notifications.tasks.send_digests", minute="0"),
        "nightly-rollup": CronJob(task="reports.tasks.rollup", minute="0", hour="7",
                                  max_runtime=30),
    })

    # settings.py
    CELERY_BEAT_SCHEDULE = registry.beat_schedule()
    CELERY_TIMEZONE = registry.timezone

    # notifications/tasks.py — note the decorator order
    @shared_task
    @registry.monitor("send-weekly-digests")
    def send_digests(): ...

``sentry_sdk`` is imported lazily, so this module is usable (and testable) without it;
``celery`` comes from the package's ``celery`` extra.
"""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    # Optional deps — only needed for typing. `MonitorConfig` is imported from
    # sentry_sdk's private _types exactly as sentry_sdk itself does; it is never
    # evaluated at runtime.
    from sentry_sdk._types import MonitorConfig

#: Sentry's own default when a monitor omits one. Both the crontab fields and the
#: monitor config are interpreted in the registry's timezone, so there is one answer.
DEFAULT_TIMEZONE = "UTC"


@dataclass(frozen=True)
class CronJob:
    """One beat-scheduled job: what to run, when, and how patient Sentry should be.

    The crontab fields use Celery's spelling and are rendered to Sentry's
    ``"m h dom mon dow"`` string from the same values, so the two can't disagree.
    ``max_runtime`` and ``checkin_margin`` are in minutes (Sentry's unit).
    """

    task: str
    minute: str
    hour: str = "*"
    day_of_month: str = "*"
    month_of_year: str = "*"
    day_of_week: str = "*"
    # Generous headroom over observed runtimes: enough to catch a genuinely wedged
    # task, not so tight that a slow check-in reads as an outage.
    max_runtime: int = 10
    checkin_margin: int = 5
    # >1 deliberately: one blip is absorbed, two consecutive misses page. A dead worker
    # is alerted one window later than a hair trigger would be, which is the trade for
    # alerts that get believed.
    failure_issue_threshold: int = 2
    recovery_threshold: int = 1

    def celery_schedule(self) -> Any:
        from celery.schedules import crontab

        return crontab(
            minute=self.minute,
            hour=self.hour,
            day_of_month=self.day_of_month,
            month_of_year=self.month_of_year,
            day_of_week=self.day_of_week,
        )

    def sentry_schedule(self) -> str:
        return (
            f"{self.minute} {self.hour} {self.day_of_month} {self.month_of_year} {self.day_of_week}"
        )

    def monitor_config(self, timezone: str = DEFAULT_TIMEZONE) -> "MonitorConfig":
        return {
            "schedule": {"type": "crontab", "value": self.sentry_schedule()},
            "timezone": timezone,
            "checkin_margin": self.checkin_margin,
            "max_runtime": self.max_runtime,
            "failure_issue_threshold": self.failure_issue_threshold,
            "recovery_threshold": self.recovery_threshold,
        }


class CronRegistry:
    """The single table beat and Sentry both read.

    Registry keys are both the beat schedule entry name and the Sentry monitor slug.
    They were already the same under the SDK's auto-registration, so keeping them
    identical means existing monitors keep their history instead of being orphaned
    by a rename.
    """

    def __init__(self, jobs: dict[str, CronJob], *, timezone: str = DEFAULT_TIMEZONE) -> None:
        self.jobs = jobs
        self.timezone = timezone

    def beat_schedule(self) -> dict[str, dict[str, Any]]:
        """The ``CELERY_BEAT_SCHEDULE`` dict, built from the registry."""
        return {
            slug: {"task": job.task, "schedule": job.celery_schedule()}
            for slug, job in self.jobs.items()
        }

    def monitor(self, slug: str) -> Any:
        """The Sentry cron decorator for ``slug``, applied *below* ``@shared_task`` so
        it wraps the function Celery actually calls.

        A no-op when Sentry isn't configured (dev/test/CI): ``capture_checkin`` funnels
        into ``sentry_sdk.capture_event``, which does nothing without an initialized
        client. The wrapped function's return value and exceptions pass through either
        way.

        An unknown slug raises rather than silently going unmonitored — a typo would
        otherwise produce a task that beat never runs and Sentry never watches.
        """
        import sentry_sdk

        try:
            job = self.jobs[slug]
        except KeyError:
            raise KeyError(
                f"No cron registry entry for '{slug}' — add it to the CronRegistry so "
                f"Celery beat and Sentry agree on its schedule."
            ) from None

        return sentry_sdk.monitor(
            monitor_slug=slug, monitor_config=job.monitor_config(self.timezone)
        )
