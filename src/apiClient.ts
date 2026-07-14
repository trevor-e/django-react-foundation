import type { TokenStorage } from './tokenStorage'

export interface RefreshResponse {
  access_token: string
  refresh_token?: string
}

export interface ApiClientOptions {
  /** The backend base URL, or a function returning it (e.g. if it can change at runtime). */
  baseUrl: string | (() => string)
  tokenStorage: TokenStorage
  /** Default `/api/auth/refresh`. Must accept `{"refresh_token": string}` and return
   * `{"access_token": string, "refresh_token"?: string}`. */
  refreshEndpoint?: string
  /** Called once, after a refresh attempt fails and tokens have been cleared — wire
   * this to redirect to a login screen. */
  onAuthFailure?: () => void
}

export class ApiRequestError extends Error {
  constructor(
    message: string,
    public readonly status: number
  ) {
    super(message)
    this.name = 'ApiRequestError'
  }
}

export interface ApiClient {
  /** Deny-by-default backends require a bearer token on every gated route; this
   * attaches one whenever we have it and is a no-op for public routes. On a 401 with a
   * refresh token available, refreshes once and retries the request once before giving up.
   * Refreshes are single-flight (per tab via a shared promise, across tabs via the Web
   * Locks API where available), so rotate-and-blacklist backends — where a refresh token
   * is strictly single-use — don't log the user out when concurrent requests 401 together. */
  request<T>(endpoint: string, init?: RequestInit): Promise<T>
}

export function createApiClient(options: ApiClientOptions): ApiClient {
  const getBaseUrl = () =>
    typeof options.baseUrl === 'function' ? options.baseUrl() : options.baseUrl
  const refreshEndpoint = options.refreshEndpoint ?? '/api/auth/refresh'

  async function performRefresh(staleAccessToken: string | null): Promise<void> {
    // Another caller (or another tab, since storage is shared) may have already
    // rotated the tokens while we waited our turn. If the stored access token is no
    // longer the one that 401'd, reuse it rather than spending the single-use
    // refresh token again.
    const currentAccessToken = options.tokenStorage.getAccessToken()
    if (currentAccessToken && currentAccessToken !== staleAccessToken) {
      return
    }

    const refreshToken = options.tokenStorage.getRefreshToken()
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
    options.tokenStorage.setTokens({
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

  function buildHeaders(init: RequestInit | undefined, token: string | null): Record<string, string> {
    const headers: Record<string, string> = { 'Content-Type': 'application/json' }
    if (init?.headers) {
      Object.assign(headers, init.headers as Record<string, string>)
    }
    if (token) {
      headers['Authorization'] = `Bearer ${token}`
    }
    return headers
  }

  async function request<T>(endpoint: string, init?: RequestInit): Promise<T> {
    const accessTokenUsed = options.tokenStorage.getAccessToken()
    const headers = buildHeaders(init, accessTokenUsed)
    let response = await fetch(`${getBaseUrl()}${endpoint}`, { ...init, headers })

    if (response.status === 401 && options.tokenStorage.getRefreshToken()) {
      try {
        await refreshAccessToken(accessTokenUsed)
      } catch {
        options.tokenStorage.clear()
        options.onAuthFailure?.()
        throw new Error('Session expired. Please login again.')
      }
      const retryHeaders = buildHeaders(init, options.tokenStorage.getAccessToken())
      response = await fetch(`${getBaseUrl()}${endpoint}`, { ...init, headers: retryHeaders })
    }

    if (!response.ok) {
      throw new ApiRequestError(`API request failed: ${response.statusText}`, response.status)
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

  return { request }
}
