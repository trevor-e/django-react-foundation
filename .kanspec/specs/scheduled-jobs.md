---
feature: One scheduled-job declaration for Celery beat and Sentry cron monitoring
code: [python/src/drf_foundation/crons.py, python/tests/test_crons.py]
---
# scheduled-jobs

## Rules
- [scheduled-jobs.single-declaration] A `CronRegistry` job declaration is the source for both Celery beat scheduling and Sentry monitor configuration. {pre-kanspec}
- [scheduled-jobs.thresholds] Monitor schedules include explicit check-in and runtime thresholds rather than inheriting provider defaults. {pre-kanspec}
- [scheduled-jobs.worker-owned] Check-in lifecycle wraps execution in the worker process; beat does not open a check-in that a separate worker must close. {pre-kanspec}
- [scheduled-jobs.decorator-order] The registry monitor wraps the task function beneath `shared_task`, preserving the expected callable and check-in lifecycle. {pre-kanspec}
- [scheduled-jobs.optional-sentry] Schedule rendering works without the Sentry extra; monitoring imports its provider lazily only when a consumer constructs the monitor decorator. {pre-kanspec}
