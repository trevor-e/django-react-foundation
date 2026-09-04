---
feature: Build identity and safe stale-tab recovery across atomic deploys
code: [src/versionSkew.ts, src/vite.js, src/vite.d.ts, template/frontend/public/boot-guard.js]
---
# deploy-skew-safety

## Rules
- [deploy-skew-safety.shared-id] The Vite integration embeds one build id in the client bundle and emits the same id in `/version.json`; SSR builds emit no duplicate version artifact. {pre-kanspec}
- [deploy-skew-safety.uncached-probe] Version probes are same-origin and `no-store`, and deployments serve `/version.json` without cache reuse. {pre-kanspec}
- [deploy-skew-safety.no-polling] Normal skew checks are activity-driven and throttled per tab rather than run by a polling timer. {pre-kanspec}
- [deploy-skew-safety.safe-reload] A known-stale healthy tab reloads only on a later route change so an open form or active view is not destroyed mid-use. {pre-kanspec}
- [deploy-skew-safety.crash-recovery] A crash may trigger a bounded probe and reload when a newer deployment exists, but retries are capped to avoid a broken-deploy loop. {pre-kanspec}
- [deploy-skew-safety.entry-guard] The pre-bundle boot guard detects missing/mistyped hashed entry assets and retries with bounded session-scoped backoff because application code has not executed yet. {pre-kanspec}
