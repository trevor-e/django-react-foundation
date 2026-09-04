---
feature: Ordered per-scope event logs and safe activity/audit recording
code: [python/src/drf_foundation/event_log.py, python/src/drf_foundation/activity.py, python/tests/test_event_log.py, python/tests/test_activity.py]
---
# event-activity-log

## Rules
- [event-activity-log.consumer-model] The package supplies abstract event-log fields and helpers but no concrete table or migration; each consumer owns its scope foreign key and uniqueness constraint. {pre-kanspec}
- [event-activity-log.sequence] Entries use a contiguous per-scope sequence with a unique `(scope, seq)` backstop, and ordered reads resume strictly after a cursor. {pre-kanspec}
- [event-activity-log.locking] Generic append helpers require the caller to hold scope-level mutual exclusion; `ActivityLog.record` centralizes that discipline with a scope-row lock. {pre-kanspec}
- [event-activity-log.commit] Realtime notification of appended events is scheduled only after the surrounding database transaction commits. {pre-kanspec}
- [event-activity-log.payload] Activity payloads describe actionable events without reproducing credential secrets; credentials are referenced only by safe identifiers such as masked prefixes. {pre-kanspec}
- [event-activity-log.tradeoff] Contiguous cursor-readable logs intentionally serialize writes per scope and are not the default for high-frequency trails that do not need resumable reads. {pre-kanspec}
