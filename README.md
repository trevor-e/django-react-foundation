# django-react-foundation

Reusable plumbing for a Django + DRF + React/Vite side project: Pydantic-inside-DRF wire
schemas, deny-by-default permissions, an auth-aware API client, and the generated-TS-types
pipeline that ties them together.

Shared by several projects rather than owned by any one of them. That is a constraint, not
a boast: nothing lands here until a second project can actually use it, which is what the
extraction gates in [`docs/blueprint.md`](docs/blueprint.md) §17 exist to enforce.

See [`docs/blueprint.md`](docs/blueprint.md) for the full stack blueprint this repo's code
is one piece of — repo layout, testing/CI/deploy conventions, and the spec-driven
(OpenSpec) change-management workflow, all meant to be reused the same way across every
project, not just this one.

## New project in one command

```sh
scripts/new-project.sh myapp ~/dev/myapp
```

stamps out `template/` — a full Django+DRF (ASGI/granian) + React/Vite project wired to
both packages, with auth, tests, CI, dev-stack tooling, and deploy config. See blueprint
§15.

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

The two packages version independently, so release tags are prefixed by side:
`py-v<version>` for the Python package, `js-v<version>` for the frontend package.
(Unprefixed `v0.x` tags predate this convention — they still resolve, but new
releases only get prefixed tags.)

**Backend (Python / uv):**

```bash
uv add "django-drf-foundation @ git+https://github.com/trevor-e/django-react-foundation.git@py-v0.10.0#subdirectory=python"
```

**Frontend (JS / pnpm):**

```bash
pnpm add "github:trevor-e/django-react-foundation#js-v0.13.0"
```

See [`python/README.md`](python/README.md) for the backend package's setup/usage, and
below for the frontend package's.

---

## Frontend package (`react-vite-foundation`, this repo's root)

An auth-aware API client, a local/prod backend URL switch, a TanStack Query key factory,
a CLI for generating TypeScript types from the backend's JSON Schema, and opt-in
subpaths: runtime head-tag management (`/seo`), a browserless build-time prerenderer
(`/prerender`), and Radix-based auth-page UI (`/auth-ui`).

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

Two auth modes, one client — pass **exactly one** of `tokenStorage` or `session`:

| | JWT mode (`tokenStorage`) | Session mode (`session`) |
|---|---|---|
| Credential | access/refresh tokens your code stores | `HttpOnly` session cookie the browser holds |
| Readable by page JS | yes — an XSS can exfiltrate a replayable token | no |
| Per request | `Authorization: Bearer` | `credentials: 'include'` + `X-CSRFToken` on unsafe methods |
| On `401` | refresh once, retry once | terminal — `onAuthFailure` fires |
| Cross-*site* frontend | works | cannot authenticate: the cookie is `SameSite=Lax` by design |

Prefer session mode (paired with `drf_foundation.session_auth`) when the only browser
client is a first-party SPA on the same site as the API. Reach for JWT mode when clients
are cross-site or native.

#### JWT mode

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
- throws `ApiRequestError` for any other non-`2xx` response — `.message` is the error
  envelope's `detail` when the body has one (generic otherwise), `.status` the HTTP
  status, and `.body` the parsed JSON error body for structured payloads (e.g. a
  stale-cursor `409`'s `{events, head}`).

#### Session mode

```ts
// src/lib/api.ts
import { createApiClient, createSessionHint } from 'react-vite-foundation'
import { API_BASE_URL } from './config'

export const sessionHint = createSessionHint() // key: session_hint
let csrfToken: string | null = null

export const setCsrfToken = (token: string) => {
  csrfToken = token
}

export const apiClient = createApiClient({
  baseUrl: API_BASE_URL,
  session: {
    getCsrfToken: () => csrfToken,
    setCsrfToken,
    // csrfEndpoint defaults to /api/auth/csrf (drf_foundation.session_auth.csrf_token)
  },
  onAuthFailure: () => {
    sessionHint.clear()
    window.location.href = '/login'
  },
})
```

In session mode `request<T>()`:
- sends `credentials: 'include'` on every call and no `Authorization` header;
- adds `X-CSRFToken` to unsafe methods only, fetching a token from `csrfEndpoint` first
  if it doesn't have one (single-flight). Reads never trigger that fetch, so a visitor who
  only browses public pages never starts a session;
- retries once on a CSRF-rejected `403` after re-fetching the token — the server rotates
  it on login/logout/password change, so a long-lived page can hold a stale one. A `403`
  that isn't a CSRF failure is passed straight through;
- treats `401` as terminal: there is nothing to refresh, so it calls `onAuthFailure` and
  throws;
- unwraps envelopes, returns `undefined` for `204`, and throws `ApiRequestError`
  otherwise, exactly as in JWT mode.

Your auth service stores the `csrf_token` each auth response returns, and sets/clears the
session hint — the non-secret "probably signed in" flag route guards and the prerender
auth gate read, since an `HttpOnly` cookie is invisible to JavaScript. It is not a
credential: the server stays the only authority, and a stale hint costs one `401` and a
redirect.

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

### 5. SEO: head tags + build-time prerendering

Two opt-in subpaths (the root export stays react-free):

```ts
// Any public page — keeps title/description/canonical/og:*/JSON-LD in sync,
// restoring on unmount. Pairs with static site-wide tags in index.html.
import { useSeo } from 'react-vite-foundation/seo'

useSeo({
  title: 'Pricing | example.com',
  description: 'What it costs.',
  canonicalUrl: 'https://example.com/pricing',
})
```

```js
// scripts/prerender.mjs — after `vite build` + a tiny `vite build --ssr` entry
// that exports renderRoute(path) via react-dom/server's renderToString.
import { prerenderSite } from 'react-vite-foundation/prerender'

prerenderSite({
  distDir: 'dist',
  siteOrigin: 'https://example.com',
  routes: PUBLIC_ROUTES, // [{ path, title, description, changefreq?, priority?, jsonLd?, image?, imageAlt? }]
  render: renderRoute,
  authGate: true, // hide the prerendered landing pre-paint for logged-in users
})
```

The serving contract is the part worth internalizing (documented at the top of
`src/prerender.ts`): `dist/index.html` stays the untouched SPA fallback; `/` is
emitted as `landing.html` behind a `_redirects` rewrite; other routes become flat
`<route>.html` files so clean-URL hosts serve the exact canonical with no
trailing-slash 308. Keep the SSR entry's import graph tiny (marketing pages only)
— rendering your whole app in Node drags every dependency into the SSG pass. No
browser is required at build time, so this runs on any CI or Pages build image.

A route may declare its own social card via `image` (+ `imageAlt`): the shell's
site-wide `og:image`/`og:image:alt` are replaced in place — scrapers prefer the
document's first `og:image`, so appending a second tag would lose — and a
same-origin `image` must exist as a file in `distDir` or the prerender throws,
so a route can't ship a preview that 404s.

`authGate` fixes the flash that contract otherwise gives logged-in users on `/`:
the static marketing HTML paints before the JS bundle runs, then the client-side
auth check redirects. The option injects a blocking head script into
`landing.html` (only) that checks localStorage for the access-token key —
presence only, the value is never read into the page — and hides `#root`
pre-paint; the app lifts the gate via `liftAuthGate()` (root export) in a mount
effect on its landing-or-app switch, once React has replaced the prerendered
DOM. When `dist/_headers` carries a `script-src 'self'` CSP, exactly that script
is allowlisted by a sha256 hash computed from the injected bytes — no
`'unsafe-inline'`, and header and script can't drift. Pass
`{ storageKey: '...' }` if your `createLocalStorageTokenStorage` overrides the
default key.

### 6. Auth-page UI (`/auth-ui`)

Opt-in, brand-free auth UI on Radix Themes — the only UI subpath (see the "does NOT
cover" note below for why it's the exception). Requires the optional peers
`@radix-ui/themes` and `@radix-ui/react-icons`; consumers that never import the
subpath don't need them.

```tsx
import { AuthLayout, PasswordField } from 'react-vite-foundation/auth-ui'

// Bind your brand once in a thin app-side wrapper:
<AuthLayout wordmark={<Link to="/">myapp.com</Link>} tagline="Your tagline.">
  {/* your login/register card content */}
</AuthLayout>

// Password input with show/hide toggle + hint/error line, accessible-name-safe:
<PasswordField label="Password" name="new-password" autoComplete="new-password"
  required minLength={8} hint="At least 8 characters."
  value={password} onChange={setPassword} error={fieldError} />
```

### 7. Light/dark appearance

A `system | light | dark` preference stored in **localStorage, never a cookie**, so a
project can hold a no-cookie-banner posture. `system` is the default and is stored as
an *absent* key, so first run needs no write.

```tsx
import { useThemePreference } from 'react-vite-foundation'

const [pref, setPref] = useThemePreference()   // re-renders on change
setPref('dark')                                 // toggles the `dark` class on <html>
```

While in `system`, OS appearance changes take effect live. Pair it with
`template/frontend/public/boot-guard.js`, which applies the class **before first
paint** — otherwise a dark-mode user sees a flash of light theme on every cold load.
That snippet can't live in the bundle: it has to run before the module entry, and a
strict CSP rules out an inline `<script>`. Keep its storage key in sync with
`THEME_STORAGE_KEY`.

The same boot file carries a **refresh-during-a-deploy guard**. If a load lands on an
`index.html` whose hashed `/assets/` files aren't servable yet (mid-deploy propagation)
or are already gone (stale cached HTML), the host serves `index.html` as the SPA
fallback for the missing asset, the MIME check blocks the entry module, and the app
never boots. Vite's `vite:preloadError` can't help — it only covers lazy chunks loaded
*after* boot, and here the entry itself never ran. The guard reloads with backoff, at
most 3 times per rolling minute (sessionStorage) so a genuinely broken deploy can't
loop.

### 8. Realtime sync (SSE doorbell → invalidate or cursor-fold)

Two flavors over one shared stream loop (reconnect backoff, hidden-tab pausing,
catch-up on every (re)connect; pairs with `drf_foundation.realtime` / `.event_log`):

```ts
// Invalidate-and-refetch (CRUD apps): a data frame or a moved head -> onChange().
const sync = createRealtimeSync({
  streamUrl: `${API_BASE_URL}/api/stream`,
  getToken: () => tokenStorage.getAccessToken(), // or credentials: 'include'
  fetchHead: () => apiClient.request<string | null>('/api/changes/head'),
  onChange: () => queryClient.invalidateQueries({ queryKey: rootKey }),
})

// Ordered event logs (games, chat, feeds): exactly-once, in-order, resumable.
const sync = createCursorSync<GameEvent>({
  streamUrl: `${API_BASE_URL}/api/wars/${id}/stream`,
  getToken: () => tokenStorage.getAccessToken(),
  fetchAfter: (cursor) => apiClient.request<GameEvent[]>(`/api/wars/${id}/events?after=${cursor}`),
  apply: (events) => store.fold(events),   // must advance getCursor() past them
  getCursor: () => store.cursor,
})
sync.start()
```

`createCursorSync` pumps until an empty page on every doorbell and every (re)connect,
skips doorbells ≤ the local cursor (your own command's echo), coalesces pumps
single-flight, retries failed pumps on the backoff schedule, and exposes `pump()` for
manual catch-up (e.g. after a stale-cursor 409). The store owns the cursor, so a page
refresh resumes from wherever its snapshot loading left it.

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
  into a shared package. One deliberate exception: `/auth-ui`, because auth pages are
  the same shell in every project and the components there are brand-free by
  construction (the consumer passes its wordmark/tagline).
- **Deploy-skew recovery** (build-id stamp + `version.json` + reload-on-skew, and the
  Cloudflare Pages boot guard) — evaluated 2026-08-27, Gate 0 failure: only adulting has
  it (`frontend/src/lib/versionSkew.ts`, `frontend/public/boot-guard.js`, the build-id
  plugin in its `vite.config.ts`); pystonks has no version stamping, no boot guard, and
  its `ErrorBoundary.tsx` is an inline widget-catch with a different contract (renders
  `error.message`, resets in place — not a root deploy-recovery path). There is nothing
  shared to extract until a second consumer builds deploy recovery of its own on a
  Pages-style host; then diff the two and move the intersection (the version-probe and
  boot-guard mechanics look portable; the error screens and their copy are per-product).
