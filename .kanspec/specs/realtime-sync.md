---
feature: Fail-soft SSE doorbells, resumable ordered synchronization, and presence
code: [python/src/drf_foundation/realtime.py, python/src/drf_foundation/presence.py, src/sse.ts, src/streamLoop.ts, src/realtimeSync.ts, src/cursorSync.ts]
---
# realtime-sync

## Rules
- [realtime-sync.doorbell] SSE carries change notifications rather than authoritative payloads; durable state and catch-up data remain in the database/API. {pre-kanspec}
- [realtime-sync.fail-soft-publish] Publishing a doorbell is fail-soft and occurs after transaction commit, so Redis failure cannot break the write path and rolled-back writes are never announced. {pre-kanspec}
- [realtime-sync.lifecycle] The client reconnects with backoff, pauses connections while the document is hidden, and runs catch-up before every connection or reconnection. {pre-kanspec}
- [realtime-sync.cursor] Cursor synchronization applies events exactly once and in order, pumps until caught up, coalesces concurrent pumps, and treats the consumer-owned cursor as authoritative progress. {pre-kanspec}
- [realtime-sync.auth] Fetch-based SSE supports bearer headers and session credentials without placing secrets in the stream URL. {pre-kanspec}
- [realtime-sync.presence] Presence is a best-effort, refcounted TTL projection of stream lifecycle; it self-heals and never gates authoritative behavior. {pre-kanspec}
