# django-react-foundation

Reusable plumbing for a Django + DRF + React/Vite side project: Pydantic-inside-DRF wire
schemas, deny-by-default permissions, an auth-aware API client, and the generated-TS-types
pipeline that ties them together. Extracted from a working production app's shared
foundation layer.

See [`docs/blueprint.md`](docs/blueprint.md) for the full stack blueprint this repo's code
is one piece of — repo layout, testing/CI/deploy conventions, and the spec-driven
(OpenSpec) change-management workflow, all meant to be reused the same way across every
project, not just this one.

## Repo layout — two packages, one repo, installed independently

```
django-react-foundation/
  docs/blueprint.md    <- the full stack blueprint (not just this repo's code)
  scripts/              <- reusable tooling (e.g. openspec-mark-tasks.py)
  package.json          <- the JS package (repo root — see "why root" below)
  src/, bin/, tests/
  python/                <- the Python package (a subdirectory)
    pyproject.toml
    src/drf_foundation/, tests/
```

**Why the JS package lives at the repo root, not in a subdirectory:** `uv`/pip's git
dependencies support a `#subdirectory=` fragment, so the Python package can be installed
straight out of `python/`. npm/pnpm's git-dependency syntax has **no equivalent** — it
only ever installs whatever's at the repo root. So the root is reserved for the package
that needs it (JS); the one with subdirectory support (Python) gets nested. Verified
concretely against a throwaway repo of this exact shape before adopting it — see
`git log` for the note, or just trust `uv add ...#subdirectory=python` below works.

**The JS package's `package.json` has a `"files"` allowlist** (`src`, `bin`, `README.md`,
`LICENSE`) — without it, a git-dependency install would clone the *entire* repo into
`node_modules`, including all of `python/` (verified: `uv.lock`, Python source, tests all
show up in `node_modules` otherwise). `files` is honored for git-dependency installs the
same way it is for `npm publish`, so `python/` never reaches a JS consumer's install.

## Install

Not published to any registry — install each package directly from git, pinned to a tag.

**Backend (Python / uv):**

```bash
uv add "django-drf-foundation @ git+https://github.com/trevor-e/django-react-foundation.git@v0.1.0#subdirectory=python"
```

**Frontend (JS / pnpm):**

```bash
pnpm add "github:trevor-e/django-react-foundation#v0.1.0"
```

See [`python/README.md`](python/README.md) for the backend package's setup/usage, and
below for the frontend package's.

---

## Frontend package (`react-vite-foundation`, this repo's root)

An auth-aware API client, a local/prod backend URL switch, a TanStack Query key factory,
and a CLI for generating TypeScript types from the backend's JSON Schema.

This package ships plain TypeScript source (no build step) — Vite/esbuild compiles it
together with the rest of your app. If you ever hit a bundler edge case with a
git-installed TS dependency, add a `tsc` build step then; not needed at this scale.

### 1. The local/prod URL switch

```ts
// src/lib/config.ts
import { resolveApiBaseUrl } from 'react-vite-foundation'

export const API_BASE_URL = resolveApiBaseUrl({
  mode: import.meta.env.VITE_API_MODE,
  isProd: import.meta.env.PROD,
  prodUrl: 'https://api.example.com',
  devUrl: 'http://localhost:8000',
})
```

### 2. The API client

```ts
// src/lib/api.ts
import { createApiClient, createLocalStorageTokenStorage } from 'react-vite-foundation'
import { API_BASE_URL } from './config'

const tokenStorage = createLocalStorageTokenStorage() // keys: auth_token / refresh_token

export const apiClient = createApiClient({
  baseUrl: API_BASE_URL,
  tokenStorage,
  onAuthFailure: () => {
    window.location.href = '/login'
  },
})

export async function getWidget(id: string) {
  return apiClient.request<Widget>(`/api/widgets/${id}`)
}
```

`request<T>()`:
- attaches `Authorization: Bearer <token>` whenever a token is present;
- on a `401` with a refresh token available, calls `POST {refreshEndpoint}` (default
  `/api/auth/refresh`) once and retries the original request once with the new token;
- refreshes are single-flight: concurrent `401`s in one tab share a single refresh call,
  tabs coordinate via the Web Locks API where available, and a caller that finds the
  stored access token already rotated (by another caller or tab) reuses it instead of
  refreshing again — safe against rotate-and-blacklist backends (e.g. simplejwt with
  `ROTATE_REFRESH_TOKENS` + `BLACKLIST_AFTER_ROTATION`), where each refresh token is
  strictly single-use;
- on refresh failure, clears both tokens, calls `onAuthFailure`, and throws;
- unwraps a `{status, data}` success envelope automatically (falls back to the raw body
  if it isn't wrapped, so this also works against non-enveloped endpoints);
- returns `undefined` for a `204`;
- throws `ApiRequestError` (carries `.status`) for any other non-`2xx` response.

### 3. Query keys

```ts
import { createQueryKeyFactory } from 'react-vite-foundation'

export const widgetKeys = createQueryKeyFactory('widgets')
// widgetKeys.detail('42') -> ['widgets', 'detail', '42']
// widgetKeys.list({ activeOnly: true }) -> ['widgets', 'list', { activeOnly: true }]
```

### 4. Generating types from the backend's JSON Schema

```jsonc
// package.json
"scripts": {
  "gen:types": "gen-types src/types/api-schema.json src/types/api.ts"
}
```

Run it after `python manage.py export_api_schema` (from the `python/` package) has
written the schema file. Wraps `json-schema-to-typescript` with `unreachableDefinitions`
and a `DO NOT EDIT` banner.

### Testing

```bash
pnpm install
pnpm test
pnpm run typecheck
```

### What this package deliberately does NOT cover

- **The `AuthService` login/register/logout flow itself** — those calls hit
  project-specific endpoints with project-specific payload shapes (e.g. dj-rest-auth's
  field-keyed validation errors). `createApiClient`/`createLocalStorageTokenStorage` give
  you the pieces (token storage + the refresh-retry loop); write the thin login/register
  wrapper per project.
- **UI components, Tailwind/Radix setup, TanStack Query provider wiring** — those are
  copy-paste-and-adapt territory (see the blueprint doc), not something worth forcing
  into a shared package.
