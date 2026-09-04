---
feature: Deny-by-default API access, browser authentication, account tokens, and abuse controls
code: [python/src/drf_foundation/permissions.py, python/src/drf_foundation/session_auth.py, python/src/drf_foundation/accounts.py, python/src/drf_foundation/throttling.py, src/apiClient.ts, src/tokenStorage.ts]
---
# authentication-security

## Rules
- [authentication-security.default-deny] DRF APIs require authentication by default, and an intentionally anonymous function view uses the greppable `public_endpoint` marker. {pre-kanspec}
- [authentication-security.ops-key] Shared task-key authorization is disabled when its configured secret is blank and compares a supplied `X-Task-Key` in constant time; mutations require staff-or-key rather than any-user-or-key. {pre-kanspec}
- [authentication-security.exclusive-mode] The frontend API client accepts exactly one browser authentication mode: JWT token storage or an HttpOnly session cookie. {pre-kanspec}
- [authentication-security.jwt-refresh] JWT requests attach the access token, coordinate refresh single-flight, retry once after a 401, and clear credentials before reporting refresh failure. {pre-kanspec}
- [authentication-security.session-csrf] Session requests include credentials, add CSRF only to unsafe methods, bootstrap it single-flight, and retry once only when a 403 is identified as CSRF rejection. {pre-kanspec}
- [authentication-security.rate-limits] Sensitive auth throttles remain IP-keyed even after login; the global token-user throttle applies only to DRF personal-token authentication. {pre-kanspec}
- [authentication-security.reset-tokens] Password-reset links use Django's stateless expiring generator and become invalid after password change or login; inspecting a valid link does not consume it. {pre-kanspec}
- [authentication-security.signed-tokens] Other signed user tokens carry a user id rather than an email, use a purpose-specific salt and expiry, and collapse malformed, unknown, tampered, and expired inputs to the same denial. {pre-kanspec}
