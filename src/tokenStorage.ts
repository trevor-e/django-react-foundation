/** Access/refresh JWT storage. Pluggable so tests (and non-browser targets) can swap
 * `localStorage` for an in-memory implementation. */
export interface TokenStorage {
  getAccessToken(): string | null
  getRefreshToken(): string | null
  setTokens(tokens: { accessToken: string; refreshToken?: string | null }): void
  clear(): void
}

export interface LocalStorageTokenKeys {
  accessTokenKey?: string
  refreshTokenKey?: string
}

/** Default localStorage key for the access token — shared with the prerender
 * auth gate (authGate.ts) so the two can't silently disagree. */
export const DEFAULT_ACCESS_TOKEN_KEY = 'auth_token'

export function createLocalStorageTokenStorage(
  keys: LocalStorageTokenKeys = {}
): TokenStorage {
  const accessTokenKey = keys.accessTokenKey ?? DEFAULT_ACCESS_TOKEN_KEY
  const refreshTokenKey = keys.refreshTokenKey ?? 'refresh_token'

  return {
    getAccessToken: () => localStorage.getItem(accessTokenKey),
    getRefreshToken: () => localStorage.getItem(refreshTokenKey),
    setTokens: ({ accessToken, refreshToken }) => {
      localStorage.setItem(accessTokenKey, accessToken)
      if (refreshToken) {
        localStorage.setItem(refreshTokenKey, refreshToken)
      }
    },
    clear: () => {
      localStorage.removeItem(accessTokenKey)
      localStorage.removeItem(refreshTokenKey)
    },
  }
}

/** A non-secret "there is probably a session" flag, for session-cookie auth.
 *
 * With an `HttpOnly` session cookie, JavaScript cannot answer "am I signed in?"
 * synchronously — but route guards and the prerender auth gate (authGate.ts) need an
 * answer before the first render, and making them async means a spinner on the first
 * paint of every public page.
 *
 * This is that answer, and it is deliberately *not* a credential: it carries no identity,
 * proves nothing, and grants nothing. The server stays the only authority — a stale hint
 * costs exactly one 401 and a redirect to login, which is what the API client's
 * `onAuthFailure` should wire up. Set it on sign-in; clear it on sign-out and on auth
 * failure.
 */
export interface SessionHint {
  isSet(): boolean
  set(): void
  clear(): void
}

/** Default localStorage key for the session hint — shared with the prerender auth gate
 * so the two can't silently disagree. */
export const DEFAULT_SESSION_HINT_KEY = 'session_hint'

export function createSessionHint(key: string = DEFAULT_SESSION_HINT_KEY): SessionHint {
  return {
    isSet: () => {
      try {
        return localStorage.getItem(key) !== null
      } catch {
        // Blocked/unavailable storage: answer "not signed in" and let the server
        // decide, rather than throwing during a render.
        return false
      }
    },
    set: () => {
      try {
        localStorage.setItem(key, '1')
      } catch {
        // Non-fatal: the session still works, the landing page may just flash.
      }
    },
    clear: () => {
      try {
        localStorage.removeItem(key)
      } catch {
        // Non-fatal, as above.
      }
    },
  }
}

/** In-memory `TokenStorage` — for tests, or any non-browser environment. */
export function createMemoryTokenStorage(): TokenStorage {
  let accessToken: string | null = null
  let refreshToken: string | null = null

  return {
    getAccessToken: () => accessToken,
    getRefreshToken: () => refreshToken,
    setTokens: (tokens) => {
      accessToken = tokens.accessToken
      if (tokens.refreshToken) {
        refreshToken = tokens.refreshToken
      }
    },
    clear: () => {
      accessToken = null
      refreshToken = null
    },
  }
}
