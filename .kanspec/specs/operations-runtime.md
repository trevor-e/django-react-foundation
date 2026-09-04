---
feature: Fail-closed production configuration and observable runtime health
code: [python/src/drf_foundation/checks.py, python/src/drf_foundation/env.py, python/src/drf_foundation/settings_helpers.py, python/src/drf_foundation/middleware.py, python/src/drf_foundation/views.py, python/src/drf_foundation/ops_status.py, python/src/drf_foundation/celery_health.py, python/src/drf_foundation/request_context.py]
---
# operations-runtime

## Rules
- [operations-runtime.fail-closed] Production checks fail closed when required secrets, hosts, origins, or security settings are missing or retain known development fallbacks. {pre-kanspec}
- [operations-runtime.bounded-connections] Database pool sizes and database/cache connection timeouts are explicit and bounded rather than relying on process-multiplied defaults. {pre-kanspec}
- [operations-runtime.proxy] Proxy middleware repairs only the known deployment protocol/header mismatch and leaves unrelated request behavior unchanged. {pre-kanspec}
- [operations-runtime.health] Stock health endpoints remain cheap and unthrottled for platform probes; deeper dependency collectors report component status without turning an outage into a hanging request. {pre-kanspec}
- [operations-runtime.celery] Broker health probes Redis first, then bound the worker ping, and do not infer worker health from Kombu pub/sub channel visibility. {pre-kanspec}
- [operations-runtime.request-context] Request correlation is propagated through the supported logging seam and cleared after each request so context cannot leak between requests. {pre-kanspec}
