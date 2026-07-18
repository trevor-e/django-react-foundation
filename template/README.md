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
  Railway `railway.json` with web-gated pre-deploy migrate.

## Deploy checklist (once per project)

1. Railway project: three services (backend/worker/beat) off this repo, root
   directory `backend`, `PROCESS` set per service; Postgres + Redis addons wired via
   `${{Postgres.DATABASE_URL}}` / `${{Redis.REDIS_URL}}`.
2. Set `SECRET_KEY` (50+ chars), `DEBUG=false`, `ALLOWED_HOSTS`,
   `CORS_ALLOWED_ORIGINS`, `FRONTEND_BASE_URL`.
3. **On the web service only**: healthcheck path `/api/health` (§11b —
   `healthcheck.railway.app` is already in ALLOWED_HOSTS and the path is exempted
   from the SSL redirect; worker/beat must NOT get a healthcheck).
4. Frontend → Cloudflare Pages (build `pnpm run build`, output `frontend/dist`),
   `VITE_API_PROD_URL` pointing at the backend's domain.
