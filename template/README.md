# __PROJECT__

Stamped out from [django-react-foundation](https://github.com/trevor-e/django-react-foundation)'s
template — Django + DRF (ASGI via granian) backend, React + Vite frontend, on the
foundation's two packages. The stack's conventions live in the foundation repo's
`docs/blueprint.md`; section references below (§) point there.

## First run

```sh
make install       # uv sync + pnpm install
make dev           # DB/Redis (docker) + migrate + granian + celery + vite
make test          # backend pytest (dockerized Postgres) + frontend tsc/oxlint
kanspec status     # work queue and anything waiting on you
```

Backend on :8000, frontend on :5173. `make dev-up` / `dev-down` / `dev-status` run the
same stack backgrounded with logs in `.artifacts/logs/` (agent-friendly).

## What's pre-wired

- **ASGI serving** (§11a): granian everywhere (`--reload` in dev), sync DRF views,
  chunked-body middleware for proxied POSTs, pooled DB connections (§1b).
- **Auth**: email-login `User`, register/login/refresh/logout/me endpoints matching
  `react-vite-foundation`'s apiClient contract (rotating refresh tokens, blacklist).
- **Wire schemas** (§3): Pydantic `Schema` classes → `make gen-api-types` →
  `frontend/src/types/api.ts`; CI fails on drift.
- **Fail-closed prod checks**: `config/checks.py` refuses to boot production with dev
  secrets/DEBUG/weakened headers; extend it per provider seam you add.
- **Deploy** (§11): one Docker image dispatched by `PROCESS` (web/worker/beat),
  Railway infrastructure as code in `.railway/railway.ts` (web-gated pre-deploy
  migrate, `watchPatterns`, healthcheck) — applied via `railway config`, not read
  at deploy time.
- **Change management** (§16): `.kanspec/` for proposals, tickets, living specs, and
  standing rules; Claude and Codex context files plus git hooks are initialized.

## Deploy checklist (once per project)

1. Railway, from the IaC file (`railway.json`/`railway.toml` are deprecated:
   new services can't use them, and existing files stop being read 2026-12-01):
   set the `github(...)` repo in `.railway/railway.ts`, run `npm install` in
   `.railway/`, create + link a Railway project (`railway init` / `railway link`),
   then `railway config plan` → `railway config apply`. That creates the three
   services (backend/worker/beat off this repo, root directory `backend`,
   `PROCESS` set per service) plus Postgres + Redis wired via
   `DATABASE_URL`/`REDIS_URL` references, with the web-gated pre-deploy migrate.
2. Set the variables the file declares as `preserve()` — `SECRET_KEY` (50+
   chars), `ALLOWED_HOSTS`, `CORS_ALLOWED_ORIGINS`, `FRONTEND_BASE_URL` — on
   Railway (dashboard or `railway variables --set`). `preserve()` keeps their
   values out of source; they must stay declared because the file is the full
   desired state (omit means delete).
3. Healthcheck: `railway.ts` sets `/api/health` **on the backend service only**
   (§11b — `healthcheck.railway.app` is already in ALLOWED_HOSTS and the path is
   exempted from the SSL redirect; worker/beat must NOT get a healthcheck).
4. Frontend → Cloudflare Pages (build `pnpm run build`, output `frontend/dist`),
   `VITE_API_PROD_URL` pointing at the backend's domain.
