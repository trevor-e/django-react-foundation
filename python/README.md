# django-drf-foundation

Pydantic-inside-DRF wire schemas, deny-by-default permissions, and generated-TS-types
plumbing for a Django + Django REST Framework API. Extracted from a working production
app's shared foundation layer.

**The load-bearing idea:** DRF stays the HTTP layer (routing, auth, throttling); Pydantic
owns the request/response *shapes*, defined once, and a frontend generates its TypeScript
types from the same source. This package is that shared layer.

## Install

Not published to PyPI — install directly from git, pinned to a tag. This package lives in
the `python/` subdirectory of the `django-react-foundation` repo (its sibling `react-vite-foundation`
frontend package lives at that repo's root — see the top-level README for why):

```bash
uv add "django-drf-foundation @ git+https://github.com/trevor-e/django-react-foundation.git@v0.1.0#subdirectory=python"
```

## Setup

1. Add `drf_foundation` to `INSTALLED_APPS` (it ships a management command; it has no
   models/migrations, so nothing else is required):

   ```python
   INSTALLED_APPS = [
       ...,
       "rest_framework",
       "drf_foundation",
   ]
   ```

2. Wire the exception handler and deny-by-default permissions:

   ```python
   REST_FRAMEWORK = {
       "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.IsAuthenticated"],
       "EXCEPTION_HANDLER": "drf_foundation.schemas.api_exception_handler",
   }
   ```

3. (Optional) Point the schema export somewhere other than the default
   `<BASE_DIR>/../frontend/src/types/api-schema.json`:

   ```python
   WIRE_SCHEMA_OUTPUT = BASE_DIR.parent / "frontend/src/types/api-schema.json"
   WIRE_SCHEMA_TITLE = "My API wire schema"  # optional, defaults to "API wire schema"
   ```

4. (Optional) Enable the shared ops-key auth tier for headless tooling:

   ```python
   TASK_TRIGGER_KEY = os.environ.get("TASK_TRIGGER_KEY", "")  # blank = disabled
   ```

## Usage

Declare wire models per app in `<app>/schemas.py`:

```python
# widgets/schemas.py
from drf_foundation.schemas import Schema

class Widget(Schema):
    id: int
    name: str
    price: float
```

Use the envelope helpers in views:

```python
from drf_foundation.schemas import ok, err, parse
from widgets.schemas import Widget

@api_view(["GET"])
def get_widgets(request):
    widgets = [Widget(id=1, name="Left widget", price=9.99)]
    return ok(widgets)  # {"status": "success", "data": [...]}
```

Open a route to anonymous access explicitly (never leave it implicit):

```python
from drf_foundation.permissions import public_endpoint

@api_view(["GET"])
@public_endpoint
def health_check(request): ...
```

Grep `public_endpoint` at any time for the complete, auditable public-route allowlist.

### Browser auth: pick one module

Two wire contracts ship here, matching the two modes of `react-vite-foundation`'s
apiClient. Mount one set of routes, not both:

- **`drf_foundation.session_auth`** — Django's session cookie. `HttpOnly`, so page
  JavaScript cannot read or leak the credential, and revocation is a session-row delete.
  DRF's `SessionAuthentication` enforces CSRF for you, on cookie-authenticated unsafe
  methods only. The trade: a frontend on a *different site* never receives the cookie, so
  it cannot authenticate at all. Pair with `session_auth_settings()`.
- **`drf_foundation.auth`** — simplejwt access/refresh pair (needs the `auth` extra). For
  cross-site or native clients. Its tokens are readable by page JavaScript, so any
  successful XSS can exfiltrate a replayable credential.

```python
# accounts/urls.py — session auth
from drf_foundation.session_auth import LoginView, csrf_token, logout

urlpatterns = [
    path("auth/csrf", csrf_token, name="auth-csrf"),
    path("auth/login", LoginView.as_view(), name="auth-login"),
    path("auth/logout", logout, name="auth-logout"),
]
```

```python
# settings.py
from drf_foundation.settings_helpers import session_auth_settings

REST_FRAMEWORK = {
    "DEFAULT_AUTHENTICATION_CLASSES": ["rest_framework.authentication.SessionAuthentication"],
    ...,
}
globals().update(session_auth_settings(cross_origin_spa=True))
```

`session_auth_settings()` sets `CSRF_USE_SESSIONS`, so the CSRF secret lives in the
session and no CSRF cookie is set — a project can ship with exactly one cookie, none of
it readable by JavaScript. The token reaches the SPA as a response value instead:
`GET /api/auth/csrf` returns it, and every endpoint that starts or ends a session returns
the rotated one (`django.contrib.auth.login` rotates it), so clients never have to
re-fetch. Registration stays project code — call `start_session(request, user)` after
creating the user.

Rate-limit anonymous auth endpoints and personal-API-token traffic:

```python
from drf_foundation.throttling import (
    CsrfBootstrapRateThrottle,
    LoginRateThrottle,
    RegisterRateThrottle,
)

class LoginView(...):
    throttle_classes = (LoginRateThrottle,)
```

```python
# settings.py
REST_FRAMEWORK = {
    ...,
    "DEFAULT_THROTTLE_CLASSES": ["drf_foundation.throttling.TokenUserRateThrottle"],
    "DEFAULT_THROTTLE_RATES": {"auth-login": "10/min", "auth-register": "10/hour", "token-user": "120/min"},
}
```

`TokenUserRateThrottle` only throttles requests authenticated via DRF's `TokenAuthentication`
(personal API tokens) — JWT, shared-key, and anonymous traffic all bypass it, so putting it
in `DEFAULT_THROTTLE_CLASSES` is safe globally.

Check Celery/Redis broker health (e.g. for an admin dashboard) — install the `celery` extra
(`django-drf-foundation[celery]`) first:

```python
from myproject.celery import app  # your own Celery app instance
from drf_foundation.celery_health import broker_health

reachable, worker_count = broker_health(app)
```

Two-stage probe (redis `PING`, then `app.control.ping()` for worker count) so a health
endpoint fails fast instead of hanging; see the module docstring for a real gotcha it
avoids (Kombu's pidbox mailbox is invisible to `PUBSUB CHANNELS`).

Export the combined JSON Schema for the frontend:

```bash
python manage.py export_api_schema           # writes the schema file
python manage.py export_api_schema --check   # CI drift guard: fails if stale, doesn't write
```

Pipe the output through `json-schema-to-typescript` (or any JSON-Schema-to-TS tool) on the
frontend side to generate typed API response/request models. Auto-discovery means any new
`<app>/schemas.py` — in any installed app — flows into the export with no registration step.

Transactional email — a themed shell, four generic kinds, and a previews drift guard:

```python
# settings.py
from drf_foundation.emails import EmailTheme
EMAIL_THEME = EmailTheme(wordmark="myapp", wordmark_suffix=".com").with_palette(accent="#3E63DD")

# anywhere
from drf_foundation.emails import render_password_reset
email = render_password_reset(reset_url, expiry_days=3)   # .subject / .text / .html
```

```bash
python manage.py render_email_previews           # writes one HTML file per kind
python manage.py render_email_previews --check   # CI drift guard, same idiom as above
```

Bundled kinds: `verification`, `password_reset`, `password_changed`, `invite`. Project
kinds extend `drf_foundation/email/layout.html` and register a fixture via
`settings.EMAIL_PREVIEWS`. Table layout with inline styles, autoescaping never
disabled, no remote assets (nothing here can become a tracking pixel).
`drf_foundation.email_provider` is the delivery seam — Django backend or Resend, always
multipart with text primary.

Scheduled jobs — one declaration feeding both Celery beat and a Sentry cron monitor:

```python
registry = CronRegistry({"nightly": CronJob(task="app.tasks.roll", minute="0", hour="7")})
CELERY_BEAT_SCHEDULE = registry.beat_schedule()

@shared_task
@registry.monitor("nightly")          # note the order: below @shared_task
def roll(): ...
```

Use this rather than `CeleryIntegration(monitor_beat_tasks=True)`, which splits a
check-in across the beat and worker processes (so short tasks report as minutes long)
and emits no thresholds (so monitors inherit Sentry's hair-trigger defaults). See the
module docstring.

## Realtime: doorbell SSE, ordered event logs, presence

Three layers, use what you need (`django-drf-foundation[realtime]` for redis-py):

- **`drf_foundation.realtime`** — `publish(redis_url, channel, msg)` (fail-soft: a
  Redis outage never breaks the write path) + `sse_response(...)` (async SSE relay with
  proxy heartbeats). SSE is a *doorbell*: no payloads on the stream, the database stays
  the mailbox. Optional per-connection lifecycle hooks (`on_open`/`on_tick`/`on_close`,
  async callables) let concerns like presence ride the stream.
- **`drf_foundation.event_log`** — the ordered-log upgrade for feeds that need
  exactly-once, in-order delivery with resume (games, chat, notifications). Subclass
  the abstract `EventLogEntry` with your scope FK + unique `(scope, seq)` constraint,
  then: `append_events(qs, rows, extra_fields=...)` (contiguous seqs — hold the scope's
  `select_for_update` lock), `events_after(qs, after, limit=...)` +
  `after_param(request)` for the `events?after=` endpoint, and
  `publish_after_commit(...)` so the doorbell only rings for committed events. Pairs
  with `createCursorSync` in the JS package.
- **`drf_foundation.presence`** — best-effort "who's connected": `PresenceTracker`
  (refcounted Redis TTL key per member; `connect`/`heartbeat`/`disconnect` map onto the
  SSE hooks; `on_flip(online)` fires only on transitions) and a sync, fail-soft
  `is_present()` for views and background jobs. Display-grade by design — TTLs
  self-heal crashes; never gate anything authoritative on it.

```python
tracker = PresenceTracker(REDIS_URL, group=f"war:{war.id}", member=str(seat),
                          on_flip=record_flip)  # async callable
return sse_response(REDIS_URL, f"war:{war.id}",
                    on_open=tracker.connect, on_tick=tracker.heartbeat,
                    on_close=tracker.disconnect)
```

## Testing

```bash
uv sync
uv run pytest
```

## Optional extras

- `django-drf-foundation[celery]` — pulls in `celery` + `redis` for
  `drf_foundation.celery_health` and `drf_foundation.crons`. Skip it if you have no
  background job queue.
- `django-drf-foundation[sentry]` — `sentry-sdk`, for `CronRegistry.monitor`. The
  import is lazy, so schedules render and test fine without it.

## What this package deliberately does NOT cover

- **Auth/registration** (JWT issuance, user model, email verification) — those tie to a
  concrete `User` model and migration history, so they're a much heavier lift to share as a
  drop-in package. Copy the pattern from a reference project instead of importing it.
- **Settings boilerplate** beyond what's listed above (`CONN_MAX_AGE`, `ruff`/type-checker
  config, etc.) — those live in the blueprint doc as copy-paste recipes, not in this package,
  since a settings module isn't meaningfully "importable" the way a schema/permission layer is.
