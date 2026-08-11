import type { TokenStorage } from './tokenStorage'

export interface RefreshResponse {
  access_token: string
  refresh_token?: string
}

/** Session-cookie auth (`drf_foundation.session_auth`), the alternative to
 * `tokenStorage`. The credential is an `HttpOnly` cookie the browser attaches itself, so
 * this client never sees it; all it carries is the CSRF token, which is worthless
 * without the cookie. The *app* owns where that token lives (auth responses hand one back
 * on every login), so storage is injected rather than assumed. */
export interface SessionAuthOptions {
  /** The token currently held, or null before the first bootstrap. */
  getCsrfToken: () => string | null
  /** Called with a token the client fetched from `csrfEndpoint`. */
  setCsrfToken: (token: string) => void
  /** Endpoint returning `{"csrf_token": ...}`. Default `/api/auth/csrf`. */
  csrfEndpoint?: string
}

export interface ApiClientOptions {
  /** The backend base URL, or a function returning it (e.g. if it can change at runtime). */
  baseUrl: string | (() => string)
  /** JWT mode: bearer tokens from storage, with a refresh/retry loop. */
  tokenStorage?: TokenStorage
  /** Session mode: cookie credentials + CSRF header. Mutually exclusive with `tokenStorage`. */
  session?: SessionAuthOptions
  /** JWT mode only. Default `/api/auth/refresh`. Must accept `{"refresh_token": string}`
   * and return `{"access_token": string, "refresh_token"?: string}`. */
  refreshEndpoint?: string
  /** Called once the session is known to be dead — after a failed refresh (JWT mode) or
   * on any 401 (session mode). Wire this to clear local session state and redirect to
   * a login screen. */
  onAuthFailure?: () => void
}

export class ApiRequestError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    /** The parsed JSON error body, when there was one — the error envelope's fields
     * (`detail`, or structured payloads like a 409's `{events, head}`). */
    public readonly body?: unknown
  ) {
    super(message)
    this.name = 'ApiRequestError'
  }
}

export interface ApiClient {
  /** Deny-by-default backends require a credential on every gated route; this attaches
   * one whenever we have it and is a no-op for public routes.
   *
   * JWT mode: on a `401` with a refresh token available, refreshes once and retries the
   * request once before giving up. Refreshes are single-flight (per tab via a shared
   * promise, across tabs via the Web Locks API where available), so rotate-and-blacklist
   * backends — where a refresh token is strictly single-use — don't log the user out when
   * concurrent requests 401 together.
   *
   * Session mode: sends cookies, adds `X-CSRFToken` to unsafe methods, and re-bootstraps
   * once on a CSRF rejection (the token rotates whenever the session does). A `401` means
   * the cookie is gone or expired — there is nothing to refresh, so `onAuthFailure` fires
   * immediately. */
  request<T>(endpoint: string, init?: RequestInit): Promise<T>
}

/** Methods the server treats as non-mutating — no CSRF token required. */
const SAFE_METHODS = new Set(['GET', 'HEAD', 'OPTIONS', 'TRACE'])

export function createApiClient(options: ApiClientOptions): ApiClient {
  const getBaseUrl = () =>
    typeof options.baseUrl === 'function' ? options.baseUrl() : options.baseUrl
  const refreshEndpoint = options.refreshEndpoint ?? '/api/auth/refresh'
  const session = options.session
  const tokenStorage = options.tokenStorage

  if ((session && tokenStorage) || (!session && !tokenStorage)) {
    throw new Error('createApiClient: pass exactly one of `tokenStorage` (JWT) or `session`')
  }

  async function performRefresh(staleAccessToken: string | null): Promise<void> {
    // Another caller (or another tab, since storage is shared) may have already
    // rotated the tokens while we waited our turn. If the stored access token is no
    // longer the one that 401'd, reuse it rather than spending the single-use
    // refresh token again.
    const storage = tokenStorage as TokenStorage
    const currentAccessToken = storage.getAccessToken()
    if (currentAccessToken && currentAccessToken !== staleAccessToken) {
      return
    }

    const refreshToken = storage.getRefreshToken()
    if (!refreshToken) {
      throw new Error('No refresh token available')
    }

    const response = await fetch(`${getBaseUrl()}${refreshEndpoint}`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ refresh_token: refreshToken }),
    })

    if (!response.ok) {
      throw new Error('Failed to refresh token')
    }

    const data = (await response.json()) as RefreshResponse
    storage.setTokens({
      accessToken: data.access_token,
      refreshToken: data.refresh_token,
    })
  }

  async function runExclusiveAcrossTabs(fn: () => Promise<void>): Promise<void> {
    if (typeof navigator !== 'undefined' && navigator.locks) {
      await navigator.locks.request('react-vite-foundation:token-refresh', fn)
      return
    }
    return fn()
  }

  let refreshInFlight: Promise<void> | null = null

  function refreshAccessToken(staleAccessToken: string | null): Promise<void> {
    refreshInFlight ??= runExclusiveAcrossTabs(() => performRefresh(staleAccessToken)).finally(
      () => {
        refreshInFlight = null
      }
    )
    return refreshInFlight
  }

  /** Fetch a CSRF token and hand it to the app's store. Deliberately lazy — called only
   * when an unsafe request needs one — so a visitor who reads public pages and never
   * mutates anything doesn't touch the endpoint, and (where the backend stores the CSRF
   * secret in the session) never receives a cookie at all. */
  async function performCsrfBootstrap(): Promise<void> {
    const auth = session as SessionAuthOptions
    const endpoint = auth.csrfEndpoint ?? '/api/auth/csrf'
    const response = await fetch(`${getBaseUrl()}${endpoint}`, {
      credentials: 'include',
      headers: { 'Content-Type': 'application/json' },
    })
    if (!response.ok) {
      throw new ApiRequestError('Failed to obtain a CSRF token', response.status)
    }
    const body = await response.json()
    const token = (body?.data ?? body)?.csrf_token
    if (typeof token !== 'string' || !token) {
      throw new Error('CSRF bootstrap returned no token')
    }
    auth.setCsrfToken(token)
  }

  let csrfInFlight: Promise<void> | null = null

  function bootstrapCsrf(): Promise<void> {
    csrfInFlight ??= performCsrfBootstrap().finally(() => {
      csrfInFlight = null
    })
    return csrfInFlight
  }

  function buildHeaders(
    init: RequestInit | undefined,
    credential: { authorization?: string | null; csrfToken?: string | null }
  ): Record<string, string> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (init?.headers) {
      Object.assign(headers, init.headers as Record<string, string>)
    }
    if (credential.authorization) {
      headers['Authorization'] = `Bearer ${credential.authorization}`
    }
    if (credential.csrfToken) {
      headers['X-CSRFToken'] = credential.csrfToken
    }
    return headers
  }

  async function unwrap<T>(response: Response): Promise<T> {
    if (!response.ok) {
      // Surface the backend's error envelope: `detail` becomes the message (so UIs
      // can show "target is out of range" instead of "Bad Request") and the parsed
      // body rides along for structured error payloads (e.g. a 409's missed events).
      let body: unknown
      try {
        body = await response.json()
      } catch {
        // not JSON — fall through to the generic message
      }
      const detail = (body as { detail?: unknown } | undefined)?.detail
      throw new ApiRequestError(
        typeof detail === 'string' && detail
          ? detail
          : `API request failed: ${response.statusText}`,
        response.status,
        body,
      )
    }

    // 204 No Content (e.g. a successful DELETE) has no body to parse.
    if (response.status === 204) {
      return undefined as T
    }

    const data = await response.json()
    // Unwrap the `{status, data}` success envelope; tolerate an already-unwrapped body
    // so this client also works against endpoints that don't use the envelope.
    return (data?.data ?? data) as T
  }

  async function jwtRequest<T>(endpoint: string, init?: RequestInit): Promise<T> {
    const storage = tokenStorage as TokenStorage
    const accessTokenUsed = storage.getAccessToken()
    const headers = buildHeaders(init, { authorization: accessTokenUsed })
    let response = await fetch(`${getBaseUrl()}${endpoint}`, { ...init, headers })

    if (response.status === 401 && storage.getRefreshToken()) {
      try {
        await refreshAccessToken(accessTokenUsed)
      } catch {
        storage.clear()
        options.onAuthFailure?.()
        throw new Error('Session expired. Please login again.')
      }
      const retryHeaders = buildHeaders(init, { authorization: storage.getAccessToken() })
      response = await fetch(`${getBaseUrl()}${endpoint}`, { ...init, headers: retryHeaders })
    }

    return unwrap<T>(response)
  }

  async function sessionRequest<T>(endpoint: string, init?: RequestInit): Promise<T> {
    const auth = session as SessionAuthOptions
    const method = (init?.method ?? 'GET').toUpperCase()
    const needsCsrf = !SAFE_METHODS.has(method)

    // No token yet (first mutation of the visit, or straight after signing out) — fetch
    // one rather than spending the request on a guaranteed 403.
    if (needsCsrf && !auth.getCsrfToken()) {
      await bootstrapCsrf()
    }

    const send = () =>
      fetch(`${getBaseUrl()}${endpoint}`, {
        ...init,
        credentials: 'include',
        headers: buildHeaders(init, { csrfToken: needsCsrf ? auth.getCsrfToken() : null }),
      })

    let response = await send()

    // The server rotates the CSRF token whenever the session changes (login, logout,
    // password change), so a long-lived page can be holding a stale one. Tell that apart
    // from a genuine permission denial by the body, then re-bootstrap and retry once.
    if (needsCsrf && response.status === 403) {
      const body = await response.clone().text()
      if (/csrf/i.test(body)) {
        await bootstrapCsrf()
        response = await send()
      }
    }

    if (response.status === 401) {
      options.onAuthFailure?.()
      throw new ApiRequestError('Session expired. Please login again.', 401)
    }

    return unwrap<T>(response)
  }

  return { request: session ? sessionRequest : jwtRequest }
}
