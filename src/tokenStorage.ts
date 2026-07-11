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

export function createLocalStorageTokenStorage(
  keys: LocalStorageTokenKeys = {}
): TokenStorage {
  const accessTokenKey = keys.accessTokenKey ?? 'auth_token'
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
