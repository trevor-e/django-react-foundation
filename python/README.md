# django-drf-foundation

Pydantic-inside-DRF wire schemas, deny-by-default permissions, and generated-TS-types
plumbing for a Django + Django REST Framework API. Shared by several projects rather than
owned by any one of them — nothing lands here until a second project can use it (blueprint
§17).

**The load-bearing idea:** DRF stays the HTTP layer (routing, auth, throttling); Pydantic
owns the request/response *shapes*, defined once, and a frontend generates its TypeScript
types from the same source. This package is that shared layer.

## Install

Not published to PyPI — install directly from git, pinned to a tag. This package lives in
the `python/` subdirectory of the `django-react-foundation` repo (its sibling `react-vite-foundation`
frontend package lives at that repo's root — see the top-level README for why):

```bash
uv add "django-drf-foundation @ git+https://github.com/trevor-e/django-react-foundation.git@py-v<latest>#subdirectory=python"
```

## Setup

1. Add `drf_foundation` to `INSTALLED_APPS`. It owns no data — no `models.py`, no
   migrations, no tables — but it is a real Django app and two things only work when it is
   registered:

   - **Management commands**: `export_api_schema`, `export_openapi`,
     `render_email_previews`. Django discovers commands from installed apps only, so
     without this the wire-schema pipeline (`gen-api-types`, `check-api-schema`) has no
     commands to run.
   - **Templates** under `templates/drf_foundation/` — the email layouts and the MCP
     consent page — found via `APP_DIRS` template loading.

   Worth knowing because of how it fails: drop it while tidying `INSTALLED_APPS` and
   nothing complains at boot. It surfaces later as an unknown `manage.py` subcommand or a
   `TemplateDoesNotExist`, neither of which points back at the edit.

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
apiClient.

**`drf_foundation.session_auth`** — Django's session cookie. `HttpOnly`, so page
JavaScript cannot read or leak the credential, and revocation is a session-row delete.
DRF's `SessionAuthentication` enforces CSRF for you, on cookie-authenticated unsafe
methods only. The trade: a frontend on a *different site* never receives the cookie, so
it cannot authenticate at all. Pair with `session_auth_settings()`.

This is the package's only auth flavour. A `drf_foundation.auth` module used to ship a
simplejwt access/refresh pair behind an `auth` extra; it was removed once both consumers
had moved to session cookies and it had zero importers. The frontend package still
supports JWT mode in its apiClient, so a cross-site or native client remains possible —
it just brings its own backend views rather than importing them from here.

**One ordering gotcha, because it costs a production debugging session.** DRF takes the
status code for an unauthenticated request from the *first* entry in
`DEFAULT_AUTHENTICATION_CLASSES`, via that authenticator's `authenticate_header()`. Stock
`SessionAuthentication` returns `None` there, so a **session-only** stack answers `403`,
not `401` — and an SPA that keys "signed out" on 401 will keep rendering as though the
user were still logged in. Lead the list with a header authenticator (an API-key or token
class) and you get 401 for free; otherwise subclass `SessionAuthentication` and override
`authenticate_header`. Not fixed in this package because both consumers lead with a header
authenticator and neither needs it (§17 Gate 0).

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

Rate-limit sensitive auth endpoints and personal-API-token traffic:

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
(personal API tokens) — session, shared-key and anonymous traffic all bypass it, so putting
it in `DEFAULT_THROTTLE_CLASSES` is safe globally. It checks
`isinstance(request.auth, rest_framework.authtoken.models.Token)` specifically: a project
whose personal tokens are a different model (one that hashes them at rest, say) gets no
throttling from it and should subclass to widen the check.

`LoginRateThrottle`, `RegisterRateThrottle` and `CsrfBootstrapRateThrottle` derive from
**`IpKeyedThrottle`**, which keys the bucket on client IP *regardless of auth state*.
Subclass it for any other endpoint that needs the same:

```python
from drf_foundation.throttling import IpKeyedThrottle

class PasswordResetRateThrottle(IpKeyedThrottle):
    scope = "auth-password-reset"   # rate comes from DEFAULT_THROTTLE_RATES
```

They used to derive from DRF's `AnonRateThrottle`, whose `get_cache_key` returns `None`
for an authenticated request — which means no throttling at all once the caller has a
session. For registration that is actively dangerous: a successful signup *signs the new
user in*, so every signup after the first from that browser was unthrottled. Both
consumers of this package independently hit this and patched it locally before it was
fixed here. **If you were relying on these classes exempting signed-in callers, they no
longer do** — that was the bug.

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

## Activity / audit logs

`drf_foundation.event_log` gives you the table shape (ordered, append-only, per-scope
facts). `drf_foundation.activity` gives you the two things every project otherwise
re-derives when using one as an **activity or audit trail**:

```python
# models.py — the table is yours (the package ships no migrations)
class AccountEvent(EventLogEntry):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
                             related_name="events")

    class Meta(EventLogEntry.Meta):
        constraints = [models.UniqueConstraint(fields=["user", "seq"],
                                               name="uniq_account_event_seq")]

# activity.py — configured once
ACTIVITY = ActivityLog(AccountEvent, scope_field="user")

ACTIVITY.record(user, "mcp.connected", {"client": "Claude", "scope": "read"})
ACTIVITY.entries(user, after=cursor)     # one ordered page; empty == caught up
ACTIVITY.head(user)                      # latest seq, 0 when empty
```

**Why not just call `append_events` directly.** It computes the next sequence as
`MAX(seq) + 1` and deliberately does not lock — the caller owns scope-level mutual
exclusion, with the unique constraint as the backstop. Forgetting that is invisible
until two things happen at once in production, at which point it is an `IntegrityError`
on a write path that had nothing to do with logging. `record()` takes the
`select_for_update` on the scope row so the discipline lives in one place.

**Know the trade before the vocabulary grows.** Contiguous sequences are what make the
log cursor-readable, and they are also what forces the lock — so every append takes a
row lock on the scope. At human-scale events (a connection, a password change, a
setting toggled) that is invisible. At high-frequency writes it is not: every audited
write serializes on that row. A trail that never needs cursor reads is cheaper on a
plain autoincrement; this module is for the case where you want the ordered, resumable
read too.

Payloads should describe what happened in terms a person can act on, and must not
reproduce credential secrets — reference a credential by its masked prefix.

## MCP: connect the app to an AI client

`drf_foundation.mcp` is a remote [MCP](https://modelcontextprotocol.io) server —
streamable-HTTP transport, a tool registry, an API-key credential store, and the OAuth
2.1 subset claude.ai's custom-connector UI needs (it takes a URL and offers no header
field, so "connect my app to Claude" is an OAuth problem whether you wanted one or not).

**Only the tools are yours to write.** Everything else is protocol.

### The five pieces

**1. Concrete models.** The package ships no migrations, so subclass the four abstract
bases and own the migration. The credential subclasses `AbstractApiKey` — the OAuth
access token *is* an API key row, so hashing, revocation UI, and per-key throttling all
apply once instead of twice.

```python
class McpApiKey(AbstractApiKey):            # hashed at rest; you add identity
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    name = models.CharField(max_length=100)        # the connecting client's name
    scope = models.CharField(max_length=16, choices=Scope.choices)

class OAuthClient(AbstractOAuthClient): ...

class AuthorizationCode(AbstractAuthorizationCode):
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="codes")
    owner = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="+")
    issued_key = models.ForeignKey(McpApiKey, on_delete=models.SET_NULL, null=True, related_name="+")

class Grant(AbstractGrant):
    client = models.ForeignKey(OAuthClient, on_delete=models.CASCADE, related_name="grants")
    owner = models.ForeignKey(Workspace, on_delete=models.CASCADE, related_name="+")
    api_key = models.OneToOneField(McpApiKey, on_delete=models.CASCADE, related_name="grant")
```

> **Do not call the resource FK `user`.** `AbstractAuthorizationCode` already declares
> `user` for the person who completed consent. In a per-user app they are the same human
> and still not the same field — use `owner` and pass
> `OAuthModels(resource_field="owner")`. `McpOAuth` rejects a colliding name when you
> construct it rather than the first time somebody clicks Approve.

**2. Tools.** A name, a sentence aimed at a model, a bounded `ToolArgs`, a handler.
Descriptions are *prompt*, not API docs: say when to reach for the tool and what its
arguments mean in everyday words. These models are outside the wire-schema pipeline, so
bound every `str` (`max_length`) and `int` (`le`) by hand.

```python
REGISTRY = registry(
    Tool(name="find_widget", description="Look up a widget by name…",
         args_model=FindArgs, handler=find_widget),
    Tool(name="add_widget", description="Add a widget…",
         args_model=AddArgs, handler=add_widget, writes=True),
)
SERVER = McpServer(
    name="myapp", version="1.0.0", registry=REGISTRY,
    instructions=lambda ctx: f"Connected as {ctx.account}. …",
    can_write=lambda ctx: ctx.scope == "read_write",
)
```

**3. A provider** — the one seam. It answers *what may this user connect*, *may they
connect this*, and *what credential does that produce*. Nothing about PKCE, redirect
matching, code single-use, or token hashing is overridable, because those are exactly
what goes silently wrong when each app writes them again.

```python
class Provider:
    scopes = (Scope("read", "Read-only", "see everything, change nothing"),
              Scope("read_write", "Read & write", "see and change things"))

    def resources(self, user): ...        # what this user may connect (one → no picker)
    def resolve(self, user, resource_id): ...   # None denies — the authorization check
    def mint(self, *, user, resource, scope, client): ...   # -> (secret, key_row)
    def replace_previous(self, *, user, resource, client): ...  # revoke, don't stack
    def revoke(self, key, *, reason): ...
```

**4. Wire it up.** `login_redirect`'s `path` must be the sign-in route your SPA actually
serves — a wrong one does not error, it renders nothing and the connection dies on a
blank page.

```python
OAUTH = McpOAuth(
    provider=Provider(),
    models=OAuthModels(client=OAuthClient, code=AuthorizationCode,
                       grant=Grant, resource_field="owner"),
    config=OAuthConfig(
        issuer=lambda: settings.PUBLIC_API_ORIGIN,   # https in production
        resource_name="MyApp",
        codec=TokenCodec(prefix="myapp_mcp_"),
        login_url=login_redirect(settings.FRONTEND_URL, path="/auth"),
        register_throttle=SomeIpThrottle, token_throttle=SomeIpThrottle,
    ),
)

ENDPOINT = mcp_endpoint(
    server=SERVER, key_model=McpApiKey, codec=OAUTH.config.codec,
    context=lambda key: Context(user=key.user, scope=key.scope),
    issuer=lambda: settings.PUBLIC_API_ORIGIN,
    realm="MyApp", select_related=("user",),
    refuse=lambda key: None if key.user.is_active else "This account is not active.",
    throttle_scope="mcp-key",      # needs a DEFAULT_THROTTLE_RATES entry
    max_body_bytes=262_144,        # None disables the cap
)

# urls.py — root-mounted; the well-known paths are fixed by spec.
urlpatterns += OAUTH.urlpatterns(mcp_view=ENDPOINT, mcp_route="mcp")
```

`throttle_scope` does more than refuse: **every** response then carries
`X-RateLimit-Limit/Remaining/Reset`. An agent that can read its remaining budget paces
itself; one that cannot finds the ceiling by hitting it, and a 429 arriving mid-conversation
is indistinguishable from the tool being broken. The 429 adds `Retry-After`, and all of
them are named in `Access-Control-Expose-Headers` so a browser-based client can actually
read them.

`max_body_bytes` caps the request body — a tools-only JSON-RPC call is small, and without
a ceiling a request still costs a full read into memory. Over the cap answers 413 as a
JSON-RPC error envelope rather than a bare body, since an MCP client cannot tell an
unparseable response from a broken server.

**5. A production check.** The discovery documents embed the issuer and clients follow
whatever they say, so refuse to boot on a non-https one:

```python
issuer_messages(settings.PUBLIC_API_ORIGIN, check_id="myapp.E010")
```

### The frontend's half

`login_url` sends a signed-out user to your SPA with the authorize URL in `?next=`. The
SPA must honor it **only** for URLs on its own API origin — the return is cross-origin
by nature, so following whatever arrives is an open redirect.

### Rules worth keeping

- **Tenancy comes from the credential.** `/mcp` takes no tenant in its path, no tool
  accepts an owner argument, and entity arguments resolve through the scoped manager
  only — so a cross-tenant id surfaces as not-found. This defeats the usual leak test: a
  URL-walking check cannot see inside JSON-RPC. Replace it with one that walks the *tool
  registry* and fails when a tool has no cross-tenant case, so new tools enroll by
  construction.
- **Tools call service functions, not views.** Otherwise validation and derived state
  drift between what the app does and what the agent does, and the agent's version is
  the one nobody is watching.
- **Templates.** `consent.html` and `oauth_error.html` are overridable via
  `OAuthConfig`; both render `resource_name`, so the defaults are usable unbranded.

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

- **Concrete models, tables and migrations.** The package ships *field definitions, not
  tables*: every model here is `abstract = True`, and the consumer subclasses it and owns
  the migration. That is not caution, it is the only shape that works, and each reason is
  visible in a consumer today:

  - **The base declares no scope FK, because it cannot know one.** `EventLogEntry` defines
    `seq`/`event_type`/`payload`/`created_at`; the subclass adds the foreign key the log
    hangs off, its table name, and the uniqueness constraint over that scope.
  - **One base, several tables.** pystonks derives `AbstractApiKey` twice —
    `users.PersonalApiKey` and `mcp_server.McpApiKey`. A shipped concrete model could only
    ever be one of them.
  - **Per-project mixins.** adulting's is `ApiKey(PublicIdMixin, AbstractApiKey)`, layering
    its own id encoding onto the base.

  The cost of the alternative is worse than the duplication it would remove, and there is
  no duplication left to remove anyway: the fields are already declared exactly once, in
  the base. Owning tables here would make a package bump a schema migration on every
  consumer — coupling a library release to a production deploy, and turning the
  can't-partially-revert problem (blueprint §17 Gate 5) from a code problem into a data
  one.

  So: **there is nothing to extract until every consumer scopes a given table the same
  way and needs exactly one of it.** If that day comes, the migration for the projects
  already holding live rows is the real cost to price, not the model definition.

- **The user model** — `AUTH_USER_MODEL` cannot be swapped after a project's first
  migration, and every real one mixes generic auth fields with product fields. An
  abstract base is the only shareable shape, and it has not been designed yet.
- **The account-flow *views*** — register, verify, reset, change-password. Their token
  mechanics *are* covered, by `drf_foundation.accounts`: `PasswordResetLink` and
  `SignedUserToken`, which is where account-takeover bugs actually live. What stays in
  the project is the view body, because that is where the product decisions are — a bot
  check, an invite token, which audit verb, which mail template. A shared view would need
  an injection point per decision, and configuring it would cost more code than the copy.

  Login *is* covered (`session_auth`), because the credential exchange is the same
  everywhere.

  **This exclusion used to rest on a different reason, and that reason has expired.** It
  said there was nothing to extract "until two projects pick the same stack", because one
  consumer hand-rolled DRF views while the other ran allauth + dj-rest-auth. That is no
  longer true: the allauth consumer dropped it and moved onto plain DRF views over
  `drf_foundation.accounts`. The stated condition was met, so the exclusion was
  re-examined rather than left standing on a lapsed premise (§17c).

  It still holds, but now on Gate 1 rather than Gate 2 — measured, not assumed. Reading
  the two `register` bodies side by side, the shared spine is four steps: reject a
  duplicate email, create the user, send a verification mail, start the session. Wrapping
  that as a shared view needs an injection point for each of: a bot check (one consumer
  only), an invite token (one only), an audit verb (one only), a username field (one
  only), which validators run inline versus in the schema, and whether the response is
  enveloped or shaped for a legacy client. Six hooks for four shared steps is the
  trench-coat test failing, not passing.

  The reset flow is closer, and still not worth it: its dangerous half — minting and
  resolving the token — is *already* extracted and used by both. What remains after
  `PasswordResetLink.resolve` is `validate_password`, `set_password`, `save`. Three lines
  of Django stdlib is not a module.

  So: **there is nothing shared to extract until the view bodies stop differing by a
  product decision per line.** That is now a countable condition — if a future proposal
  claims otherwise, it should state the injection-point count it is arguing down from
  six, with the two bodies quoted.
- **Settings boilerplate** beyond what's listed above (`CONN_MAX_AGE`, `ruff`/type-checker
  config, etc.) — those live in the blueprint doc as copy-paste recipes, not in this package,
  since a settings module isn't meaningfully "importable" the way a schema/permission layer is.
