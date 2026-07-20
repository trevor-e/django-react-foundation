# Django + React side-project blueprint

A distillation of a working production app's patterns into a **repeatable template** for
future projects — the opinionated stack, the conventions, and the copy-pasteable recipes.
The headline is the **shared wire-schema → frontend types** pipeline, now installable
directly from this repo's two packages (see the top-level [README](../README.md)) rather
than copy-pasted; the rest is the supporting scaffolding that makes it pleasant to work in.

> This is a living checklist, not a framework. Lift the pieces you want. The wire-schema
> and API-client pieces are installable packages (`python/` and repo root — see the
> top-level README); everything else here is a copy-paste recipe.

---

## 1. Stack (opinionated defaults)

| Layer | Choice | Why |
|---|---|---|
| Language/runtime | Python 3.13+ (a production reference: 3.14) | Modern typing (`X \| None`, PEP 695 generics/`type` aliases). |
| Web framework | **Django + DRF** | Batteries-included; DRF for auth/throttle/browsable admin CRUD. |
| Wire serde | **Pydantic v2 *inside* DRF** | Static types + runtime validation + JSON-Schema export (→ frontend types). |
| Package mgmt | **uv** (backend), **pnpm** (frontend) | Fast, lockfile-first, reproducible. |
| DB | PostgreSQL (source of truth) | For OLAP, reach for **embedded DuckDB before a separate service**. See §1a. |
| Async work | Celery + Redis | Only if you have scheduled/background jobs. |
| Type check | **pyrefly** (backend), **tsc** (frontend) | A real gate, not advisory. |
| Lint/format | **ruff** (backend), **eslint** (frontend) | One tool, fast. |
| Frontend | React + TypeScript + Vite | + Tailwind/Radix for UI, TanStack Query for data. |
| Deploy | Railway (backend) + Cloudflare Pages (frontend) | Git-push auto-deploy on both. |
| CI | GitHub Actions | tests + lint + **schema-drift guard**. |
| Change management | **OpenSpec** | Spec-driven proposals for non-trivial changes. See §16. |

The load-bearing idea: **DRF stays the HTTP layer; Pydantic owns the data shapes; the
Pydantic models are the single source of truth that the frontend's TS types are generated
from.** Everything below supports that.

### 1a. Analytics/OLAP: embedded DuckDB before a separate service

**Default to an in-process OLAP engine (DuckDB) that rebuilds from Postgres on demand.
Do not stand up a separate columnar datastore (ClickHouse, Druid, etc.) unless you've hit
a wall that actually requires one.** A prior version of the reference app started on
ClickHouse and deliberately migrated *off* it to embedded DuckDB; don't reintroduce a
standalone OLAP service without a reason from the list below.

Why embedded-first is the right default at side-project / single-node scale:

- **Postgres stays the single source of truth.** The analytics engine is a derived,
  disposable cache — it holds no authoritative state.
- **No new service to run, secure, network, or pay for.** No separate container, no
  private-networking hop, no broker, no health check, no extra deploy target.
- **No sync pipeline and no analytics migrations.** The denormalized table is rebuilt
  in-process on demand (lazily, when a data fingerprint changes; freshness gated by a short
  TTL). Schema lives in code, not in a second migration system.
- **Queries run in the Django worker** — plain SQL over an in-memory table, fast enough for
  interactive endpoints at this data size.

Only reach for a standalone OLAP service when you genuinely outgrow embedded:

- The working set no longer fits in a single node's memory/disk (≫ tens of GB).
- You need high-QPS concurrent analytical serving across many users, or a rebuild-from-source
  is too slow/expensive to do in-process.
- Continuous high-volume ingestion, or sub-second queries over billions of rows, where a
  purpose-built columnar cluster earns its operational cost.

Below those thresholds, the embedded engine wins on simplicity every time. If you think you
need the cluster, write down which threshold you crossed first.

### 1b. Database connections: pooled, not persistent

Reusing a connection instead of paying a fresh TCP+auth handshake per request matters
either way; *how* depends on the server model.

- **Threaded WSGI** (gunicorn `--threads`): `CONN_MAX_AGE` + `CONN_HEALTH_CHECKS`
  works, because worker threads are long-lived.
- **ASGI** (the default serving stack, §11a): each request's sync code runs on its own
  short-lived thread (asgiref `ThreadSensitiveContext`), so thread-affine persistent
  connections strand and *leak*. Use psycopg3's built-in pool instead — it checks
  connections out per use and owns reconnection. Django rejects `pool` combined with
  `CONN_MAX_AGE`/`CONN_HEALTH_CHECKS`.

```python
DATABASES = {"default": {...}}
# Explicit bounds, always: bare `"pool": True` means a FIXED pool of 4 opened eagerly
# — and it's per PROCESS. Web, beat, and every Celery prefork child each get one, so
# defaults × an uncapped worker (see §11a's --concurrency gotcha) can brush Postgres's
# ~100-connection limit while idle.
DATABASES["default"].setdefault("OPTIONS", {})["pool"] = {
    "min_size": 1, "max_size": 5, "timeout": 10,  # psycopg[binary,pool]
}
```

### 1c. Bound every dial: connect timeouts on DB and cache

Field lesson (2026-07-19, pystonks on Railway): a platform-mesh route black-holed —
DNS resolved, SYNs silently dropped, no RST. Every DB connect then hangs for the OS
default (~130s), each hung request permanently occupies a worker, and with a small
worker pool the whole site freezes while looking "up" (CORS preflights and 404s,
which never touch the DB, still answer). Zero error logs, because nothing ever
*fails* — it just waits.

Two rules, both encoded in the helpers so they flow with pin bumps:

- **Postgres**: the pool's `timeout` bounds waiting for a *slot*; `connect_timeout`
  bounds the *dial*. You need both — `pooled_database()` sets `connect_timeout=5`
  alongside the pool, which migrations and Celery tasks inherit too (they use the
  same `DATABASES`).
- **Redis cache**: redis-py's default is `socket_timeout=None` — block forever.
  Django's built-in `RedisCache` passes `OPTIONS` through to the connection pool, so
  `redis_cache()` sets `socket_connect_timeout`/`socket_timeout` (2s). Without it,
  any cache-touching path (DRF throttle counters, sessions) inherits the unbounded
  hang when the route to Redis dies.

The failure you want when infrastructure breaks is *loud and fast*: 500s in the
logs within seconds, a liveness endpoint that still answers (§11b), and worker
threads that recycle — not a silent wedge you diagnose from the absence of logs.

---

## 2. Repo layout

```
repo/
  backend/
    pyproject.toml          # deps + ruff + pyrefly config (single file)
    uv.lock
    manage.py
    Dockerfile              # uv sync; runs migrations via platform pre-deploy
    entrypoint.sh           # PROCESS-role dispatch (web/worker/beat)
    <project>/              # settings.py, test_settings.py, urls.py (schemas/wire_schema/permissions come from django-drf-foundation)
    <app>/                  # models.py, views.py, urls.py, schemas.py, admin.py, tests live in backend/tests
    tests/                  # conftest.py + integration tests (real DB)
  frontend/
    src/types/api.ts        # GENERATED from the backend schema — do not edit
    src/types/api-schema.json
    src/lib/api.ts          # client; consumes the generated types + react-vite-foundation
    package.json            # `gen:types` script
  docs/                     # architecture + ADRs (see §12)
  openspec/                 # spec-driven change proposals (see §16)
  Makefile                  # the workflow front door (see §9)
  railway.json              # build/deploy config
  .github/workflows/        # CI
```

Keep **all tooling config in `pyproject.toml`** (ruff + type-checker + pytest), one place
to copy into the next project.

---

## 3. The wire-schema pipeline (the crown jewel)

Goal: define each request/response shape **once**, in Python, and have both the backend
runtime and the frontend types derive from it. **This is now an installable package**
(`django-drf-foundation`, this repo's `python/` — see the top-level README for the exact
`uv add` command), not a copy-paste recipe. What it gives you:

### 3a. Core helpers — `drf_foundation.schemas`

A base model, a standard envelope, and three helpers that keep views terse:

```python
from drf_foundation.schemas import Schema, ok, err, parse

class Widget(Schema):
    id: int
    name: str

@api_view(["GET"])
def get_widgets(request):
    return ok([Widget(id=1, name="Left widget")])  # {"status": "success", "data": [...]}
```

Rules that kept it honest in the reference implementation:
- **`mode="json"` is mandatory** so `datetime`/`date`/`Decimal` serialize as strings,
  byte-identical to hand-built dicts.
- **Keep computation in the view** (`round`, `isoformat`, fallbacks); models are pure
  holders, declared in the **same field order** as any pre-existing JSON.
- Use a `Number = int | float` type alias for fields that may be emitted as either (a
  `round(x, 2) or 0` result) — Pydantic's smart union preserves `0` vs `15.0` exactly.
- **Render validation 400s as your envelope** via a *narrow* `EXCEPTION_HANDLER` that
  handles your one exception type and **delegates everything else to DRF unchanged** — so
  401/403/404/throttle shapes don't change.

Per-app models live in `<app>/schemas.py`, importing `Schema`/`Number` from the package.

### 3b. Export — `drf_foundation.wire_schema` + `export_api_schema`

Auto-discovers every `Schema` subclass across every app in `INSTALLED_APPS` with a
`schemas.py` and emits one combined JSON Schema. `python manage.py export_api_schema`
writes it; `--check` fails instead of writing (the CI drift guard). No per-app
registration step — add a `schemas.py` to any app and its models flow through.

> **Gotcha the package already handles:** Pydantic stamps a `title` annotation on every
> field, which makes `json-schema-to-typescript` emit noisy `Status1`/`Message2` aliases —
> so the package strips `title`, but **only annotation titles**, never a property/`$defs`
> key literally named `title`. Output is deterministic (sorted keys, trailing newline) so
> "stale" is a clean `git diff`.

### 3c. Generate frontend types

```jsonc
// frontend/package.json — gen-types ships as a bin in react-vite-foundation
"gen:types": "gen-types src/types/api-schema.json src/types/api.ts"
```

`make gen-api-types` runs both halves. The client (`src/lib/api.ts`) then **aliases its
public types to the generated ones** instead of hand-rolling them, so a backend change
breaks the frontend build if incompatible.

### 3d. Drift fails CI two ways

- `make check-api-schema` in the lint job (Python-only).
- A pytest assertion that the committed JSON equals a fresh export (catches it in the test
  job too).

**Workflow when you change a model:** `make gen-api-types`, commit `api-schema.json` +
`api.ts`. That's the whole contract.

---

## 4. Permissions: deny-by-default + a greppable allowlist

Also ships in `django-drf-foundation` (`drf_foundation.permissions`).
`REST_FRAMEWORK["DEFAULT_PERMISSION_CLASSES"] = ["...IsAuthenticated"]`, then open the
intentional public surface with one self-documenting decorator so the public routes are a
single grep:

```python
from drf_foundation.permissions import public_endpoint

@api_view(["GET"])
@public_endpoint
def health_check(request): ...
```

`grep -rn public_endpoint` is the complete, auditable list of anonymously-reachable routes.

### 4a. A second auth tier: a shared ops key for headless automation

JWT-admin is the right gate for a human at a dashboard, but it's painful for ops tooling
(curl, cron, an MCP-driven agent) that has no browser to mint a token. The pattern that's
paid off: accept **either** a staff JWT **or** a shared secret in a header, via one
permission class you can hang on any endpoint you want drivable headlessly —
`drf_foundation.permissions.IsAdminUserOrTaskKey` / `IsAuthenticatedOrTaskKey`, backed by
`TASK_TRIGGER_KEY` in settings.

Rules that matter:
- **Constant-time compare** (`hmac.compare_digest`); **refuse outright when the secret is
  unset** so a blank env var can't authorize a blank header.
- **One comparison helper**, reused by every gate — never two divergent copies of a
  security check.
- **Keyed calls carry no user**, so stamp audit columns (`created_by`/`updated_by`) `null`
  rather than assuming `request.user` is real.
- **The key's blast radius = everything it gates.** Scope it to ops-level actions, not
  endpoints that read user content.
- **Source the secret from the platform's secret store, never the repo.**

---

## 5. Settings: one base + a thin test override

- `settings.py` reads config from `os.environ` with sane defaults (12-factor).
- `test_settings.py` does `from .settings import *` then overrides: a Docker Postgres on a
  **non-default port** (so tests run alongside dev), `MD5PasswordHasher`, LocMem cache (no
  Redis), throttling off, console email. `conftest.py` sets `DJANGO_SETTINGS_MODULE`.

---

## 6. Type checking as a gate

pyrefly configured in `pyproject.toml` (`project-includes`, exclude migrations/tests,
`replace-imports-with-any` for untyped third-party apps). The discipline that pays off:
**zero new suppressions** — every `# pyrefly: ignore` is justified inline (ORM-dynamic
accessors, query annotations) and adding one is a deliberate, reviewable act. `make
typecheck` is a real gate, not advisory.

---

## 7. Lint/format: ruff, opinionated

Enable beyond the defaults: `ANN` (type hints), `I` (import sort), `DJ` (Django), `B`,
`UP`, `SIM`, `RUF`. Per-file ignores for `tests/*` (no `ANN`) and `migrations/*`.
**Scope formatting to changed files** — `ruff format .` will reformat the whole tree and
balloon your diff.

---

## 8. Testing: real DB, minimal mocking

- Integration tests hit a **real Postgres** in throwaway Docker containers
  (`docker-compose.test.yml`, ephemeral, no volumes, non-default port). The Makefile
  auto-starts/stops them.
- Assert on **response shapes** (these are your regression gate for any serde change).
- A `conftest.py` of small, composable fixtures: `api_client`, `authenticated_client`,
  `admin_client`, `user`/`admin_user`/`superuser`, and `make_*` factory fixtures that
  return builder functions. Add **golden tests before migrating** a thinly-covered endpoint.
- Mock only true externals (paid APIs, third-party HTTP) — e.g. a fake service object
  injected via `monkeypatch.setattr`.

---

## 9. Makefile is the front door

One memorable command per task; everyone (and every agent) uses these, not raw
`uv`/`pnpm`/`docker`:

```
make install        make dev            make test           make test-backend
make lint           make typecheck      make migrate        make makemigrations
make gen-api-types  make check-api-schema                   make shell / make manage CMD="..."
```

Targets encapsulate the Docker host quirks, test-container lifecycle, and multi-step flows.

---

## 10. CI (GitHub Actions)

Three cheap jobs on push/PR to `main`:
1. **backend-tests** — `make test-backend` (spins up the test DB).
2. **lint** — `make lint` **+ `make check-api-schema`** (the drift guard).
3. (frontend tests/lint when you have them).

The schema-drift guard is the one non-obvious addition — it's what keeps "defined once"
true over time.

---

## 11. Deploy: git-push, no manual step

- **Backend → Railway**, `railway.json`: `DOCKERFILE` builder, `watchPatterns:
  ["backend/**", ...]` so frontend-only commits don't redeploy it, and a
  **`preDeployCommand: uv run python manage.py migrate --noinput`** so migrations run
  *before* new containers replace old ones (safe rolling deploys).
- **Dockerfile installs via `uv sync`** (pyproject + uv.lock) — the lockfile is the source
  of truth (don't rely on a stale `requirements.txt`).
- **`entrypoint.sh` dispatches on a `PROCESS` env var** (`web`/`worker`/`beat`) so the
  same image runs every role; only `web` collects static; roles do **not** migrate on boot
  (the platform pre-deploy does).
- **Frontend → Cloudflare Pages**, auto-built from the same repo, calls the backend at a
  custom `api.` domain. Its build is `tsc && vite build` (not lint), so a clean build is
  what gates the deploy.

### 11a. App server: Granian/ASGI (same server in dev and prod)

Serve `config.asgi:application` with **Granian** everywhere — prod web role and dev
(`--reload`, via the `granian[reload]` dev-only extra) — so async behavior (SSE
streams, async views) is testable locally. App code stays sync DRF; only genuinely
streaming views are written async. Hard-won flag rules:

- `--interface asginl` — ASGI *without* the lifespan protocol, which Django doesn't
  implement. Plain `asgi` boots with a warning; `asginl` says what you mean.
- **Never pass `--blocking-threads` on ASGI.** It's a WSGI-mode knob and granian
  hard-errors on >1 — as a crash-loop *in prod only* if dev never passed it. Sync-view
  concurrency under ASGI comes from asgiref's per-request threads inside Django;
  bound it with `--backpressure` if it ever needs a cap (remember each open SSE
  stream holds a backpressure slot for its lifetime).
- **`--workers-kill-timeout 5` is mandatory once anything streams.** Granian's
  graceful stop waits for in-flight requests, and an open SSE stream never finishes —
  without the timeout, every redeploy/reload wedges on the first connected client.
- **`--respawn-failed-workers`** (default off): with `--workers 1`, a crashed worker
  otherwise leaves a live container serving nothing — invisible to the platform's
  restart policy, which watches the main process.
- Dev `--reload` should ignore tool churn: `--reload-ignore-dirs .ruff_cache
  --reload-ignore-dirs .pytest_cache --reload-ignore-patterns '\.tmp\.'`.
- **Proxied chunked bodies**: proxies (Cloudflare orange-cloud) re-frame POSTs as
  `Transfer-Encoding: chunked` with no `Content-Length`; DRF treats missing
  CONTENT_LENGTH as an empty body even though Django's ASGI handler buffered it fine.
  Install `drf_foundation.middleware.ChunkedContentLengthMiddleware` first in
  `MIDDLEWARE`. Testing gotcha: curl only sends genuine chunked framing with
  `--http1.1`; over HTTP/2 a manual `Transfer-Encoding` header embeds framing bytes
  into the body and masquerades as a server bug.
- Celery on the same host image: **always pass `--concurrency`** — prefork defaults
  to the *host's* core count, which on shared platforms (Railway) means e.g. 32
  Django children (~4GB idle) for a queue doing nothing.

### 11b. Deploy healthchecks: worth it, and three silent killers

Set the platform healthcheck (Railway: `healthcheckPath=/api/health`) so a deploy that
can't serve **never takes traffic** — without it, "container started" counts as
success, every deploy has a 502 cutover window, and a crash-looping build replaces a
working one. But the probe fails *silently* three ways; wire all three before
enabling:

1. **ALLOWED_HOSTS**: Railway probes with `Host: healthcheck.railway.app` — add it to
   `ALLOWED_HOSTS` or every probe 400s.
2. **You won't see those 400s**: Django routes `django.security.DisallowedHost` to
   the *null log handler* by default. A host-rejected healthcheck produces zero log
   lines while the deploy times out.
3. **SSL redirect**: probes arrive as plain HTTP with no `X-Forwarded-Proto`, so
   `SECURE_SSL_REDIRECT` 301s them (also unlogged). Exempt the path:
   `SECURE_REDIRECT_EXEMPT = [r"^api/health$"]` (leading slash stripped).

Verify locally before shipping: boot granian with prod-mode env and assert the probe
shape passes — `curl -H 'Host: healthcheck.railway.app' http://localhost:8000/api/health`
→ 200, any other path → 301.

---

## 12. Docs discipline (so this stays true)

- A **docs table** at the top of `CLAUDE.md`/README maps "task → which doc to read first."
- **ADRs** for load-bearing decisions: the decision, the alternatives rejected, and the
  hard constraints.
- The rule: **when you change behavior, update the matching doc in the same change.**
- A `CLAUDE.md` (or `AGENTS.md`) so coding agents inherit all of the above as standing
  instructions.

---

## 13. Frontend (React) patterns

Also installable — `react-vite-foundation` (this repo's root, see the README):

- **One typed client** (`createApiClient`): a single wrapper does all the cross-cutting
  work, and every endpoint function goes through it —
  - attaches the JWT from token storage;
  - on a `401`, transparently calls the refresh endpoint and **retries the request once**
    (so components never deal with token expiry);
  - unwraps the `{status, data}` envelope and handles `204`.
  - Return types are the **generated wire types** (§3) — the client can't drift from the API.
- **TanStack Query for all server state** (no Redux/global store for fetched data). A
  central `createQueryKeyFactory` (hierarchical, `as const`) keeps cache keys typed and
  consistent; thin hooks wrap the client fns. Tune `QueryClient` defaults to the data —
  long `staleTime` for slow-changing data, a `retry` predicate that skips 4xx except 401,
  `keepPreviousData` for paginated lists.
- **Auth**: access + refresh JWTs in token storage; the 401→refresh→retry loop lives in
  the client wrapper, not in components. The login/register calls themselves stay
  project-specific (different backends return different validation-error shapes) — write
  a thin wrapper per project.
- **Vite** with an `@ → src` path alias; UI from a component lib (Radix) + Tailwind; reach
  for zod **only at the trust boundary** if you want runtime validation layered on the
  generated static types.

## 14. Local vs prod: one switch, both targets

You'll test against prod often, so pointing the frontend at either backend should be a
one-flag move. **A single `config.ts` is the only place the base URL is decided** — derived
by default, overridable explicitly (use `resolveApiBaseUrl` from `react-vite-foundation`):

```ts
// src/lib/config.ts — the single source of truth for the backend URL
import { resolveApiBaseUrl } from 'react-vite-foundation'

export const API_BASE_URL = resolveApiBaseUrl({
  mode: import.meta.env.VITE_API_MODE,
  isProd: import.meta.env.PROD,
  prodUrl: 'https://api.example.com',
  devUrl: 'http://localhost:8000',
})
```

Default: `vite dev` → local backend, `vite build` → prod. `VITE_API_MODE` overrides either
way, baked into npm scripts + Makefile targets so you never edit code to switch:

| Command | Frontend runs as | Talks to |
|---|---|---|
| `pnpm dev` · `make dev-frontend` | local dev server | **local** backend (`:8000`) |
| `pnpm dev:prod` · `make dev-frontend-prod` | local dev server | **prod** API |
| `pnpm build` (`build:prod`) | prod bundle | prod API (Cloudflare default) |
| `pnpm build:local` | prod bundle | **local** backend (smoke-test a prod build) |

```jsonc
// package.json — the override is just an env var in front of the normal command
"dev:prod":    "VITE_API_MODE=production vite",
"build:local": "VITE_API_MODE=local tsc -b && vite build",
```

Why this shape:
- **No `.env` juggling for the base URL** — it's derived with one explicit override knob.
  Keep real secrets in a gitignored `.env.local` and read them via `import.meta.env.VITE_*`.
- **`dev:prod` is the workhorse** for iterating UI against real prod data; **`build:local`**
  catches prod-build-only breakage (e.g. stricter `tsc`) against a local backend before you ship.
- The backend must **CORS-allow both** origins (the local dev server and the prod frontend).
- Requests are identically shaped regardless of target, so a bug seen against prod reproduces
  locally by flipping one flag.

---

## 15. Bootstrapping a new project — one command

```sh
scripts/new-project.sh <name> <target-dir>
```

stamps out `template/` — a complete working project: ASGI serving (§11a) with every
hard-won granian flag, pooled DB (§1b), healthcheck-safe security headers (§11b),
email-login auth matching the apiClient contract, wire-schema pipeline (§3), fail-closed
prod checks, Makefile/dev-stack scripts, CI, Dockerfile + railway.json. `make install`,
`make dev`, `make test` work immediately; the stamped README carries the once-per-project
deploy checklist. The template pins both foundation packages; bump the pins to adopt
fixes.

The original by-hand checklist, kept for understanding what the template gives you
(and for retrofitting an existing project):

1. `uv init` backend; add Django, DRF, pydantic, dev group (pytest, pyrefly, ruff, stubs).
2. `uv add "django-drf-foundation @ git+https://github.com/trevor-e/django-react-foundation.git@v0.1.0#subdirectory=python"`
   — add `drf_foundation` to `INSTALLED_APPS`, wire the exception handler (§3, §4). No
   file-copying needed.
3. Register deny-by-default permissions in settings; add `test_settings.py`.
4. Put ruff + pyrefly + pytest config in `pyproject.toml`.
5. `docker-compose.test.yml` (throwaway Postgres, non-default port) + `conftest.py` fixtures.
6. Frontend: Vite + TS (`@→src` alias); `pnpm add "github:trevor-e/django-react-foundation#v0.1.0"`
   for the API client + query-key factory + `gen-types` CLI (§13, §14, §3c) — again, no
   file-copying.
7. `Makefile` with the targets in §9 (including `gen-api-types` / `check-api-schema`).
8. CI: tests + lint + schema-drift.
9. `Dockerfile` (`uv sync`) + `railway.json` (`watchPatterns`, pre-deploy migrate) +
   `entrypoint.sh` (`PROCESS` roles) — **copy `templates/entrypoint.sh` from this repo**;
   its granian/celery flags encode §11a's hard-won rules. Then wire §11b's healthcheck
   (ALLOWED_HOSTS probe host, redirect exemption) before enabling it.
10. Seed `docs/` with an architecture doc + this blueprint, and a `CLAUDE.md` docs table.
11. `openspec init` (or copy an existing `openspec/` skeleton) + add the CLAUDE.md snippet
    in §16 so change management is spec-driven from day one.

---

## 16. Spec-driven change management (OpenSpec)

Track non-trivial changes with [OpenSpec](https://openspec.dev) instead of editing ad hoc:
`openspec/specs/` holds current-behavior specs, `openspec/changes/` holds in-flight
proposals (proposal/design/tasks + delta specs, one directory per change).

**Workflow**: propose → apply → archive.
- **Propose**: draft `proposal.md` (why + what + which capabilities), `design.md` (how,
  for cross-cutting/risky changes), delta `specs/<capability>/spec.md` files (ADDED/
  MODIFIED/REMOVED requirements, each with at least one scenario), and `tasks.md` (a
  checkbox list the apply phase tracks progress against).
- **Apply**: work through `tasks.md` top to bottom, checking items off as you go. Mark
  tasks done with a small reviewable script rather than ad hoc edits (see below) — keeps
  task-state changes auditable instead of one-off `sed`/inline-python edits.
- **Archive**: once all tasks are done and verified, archive the change — this syncs its
  delta specs into `openspec/specs/` (the new current-behavior baseline) and moves the
  change directory out of the active `changes/` list.

**Use dedicated tools for each phase** (`openspec-propose`, `openspec-apply-change`,
`openspec-archive-change`, or `openspec-explore` for fuzzy ideas/unfamiliar code before a
formal proposal, `openspec-sync-specs` to sync specs mid-flight without archiving) rather
than hand-editing the `openspec/` folders — they encode the schema and task-tracking format
so it stays consistent across changes and across projects.

**Recommended CLAUDE.md snippet for a new project** (adapt the paragraph break, keep the
substance):

```markdown
## Spec-driven change management (OpenSpec)

Non-trivial changes are tracked with [OpenSpec](https://openspec.dev) in `openspec/`:
`openspec/specs/` holds current-behavior specs, `openspec/changes/` holds in-flight
proposals. Use the `openspec-propose`, `openspec-apply-change`, `openspec-archive-change`
skills (or `openspec-explore` for fuzzy ideas / unfamiliar code areas) rather than editing
those folders by hand.

**Default behavior**: for any non-trivial feature or change (not a one-line fix),
proactively run the propose → apply → archive cycle yourself via those skills before
implementing — don't wait to be asked. Skip this for trivial/mechanical edits (typo
fixes, config tweaks, one-liners).

**Mechanics**: the CLI is `npx -y @fission-ai/openspec <cmd>` (no `openspec` binary on
PATH). Mark tasks done with `scripts/openspec-mark-tasks.py <change> <task-id>... [--undo]`
(copy from this repo's `scripts/`). Archive with `openspec archive <change> -y` — it also
syncs delta specs into `openspec/specs/`.
```

**Mechanics**: the CLI is `npx -y @fission-ai/openspec <cmd>` — no global `openspec` binary
needed. `scripts/openspec-mark-tasks.py` in this repo flips `- [ ]`/`- [x]` checkboxes in a
change's `tasks.md` without ad hoc `sed`/inline-python edits; copy it into a new project's
`scripts/` directory.

**Why this belongs in the blueprint, not just one project's CLAUDE.md**: the propose →
apply → archive discipline is exactly as reusable as the wire-schema pipeline — it's a
*process* convention, not project-specific code, and its payoff compounds the same way
across every project that adopts it. The skill definitions themselves are Claude Code
skills (installable at `~/.claude/skills/` to be available in every project regardless of
which repo you're in, or per-project at `.claude/skills/`); this section documents the
convention so a fresh project's `CLAUDE.md` can adopt the same discipline immediately.
