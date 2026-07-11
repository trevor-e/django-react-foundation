import { beforeEach, describe, expect, it, vi } from 'vitest'
import { createLocalStorageTokenStorage, createMemoryTokenStorage } from '../src/tokenStorage'

// vitest's default 'node' environment has no `localStorage` global; a minimal in-memory
// polyfill is enough to exercise createLocalStorageTokenStorage's key-naming behavior
// without adding jsdom as a dependency.
function installLocalStoragePolyfill() {
  const store = new Map<string, string>()
  vi.stubGlobal('localStorage', {
    getItem: (key: string) => store.get(key) ?? null,
    setItem: (key: string, value: string) => store.set(key, value),
    removeItem: (key: string) => store.delete(key),
  })
}

describe('createLocalStorageTokenStorage', () => {
  beforeEach(() => {
    installLocalStoragePolyfill()
  })

  it('defaults to auth_token/refresh_token keys', () => {
    const storage = createLocalStorageTokenStorage()
    storage.setTokens({ accessToken: 'a1', refreshToken: 'r1' })
    expect(localStorage.getItem('auth_token')).toBe('a1')
    expect(localStorage.getItem('refresh_token')).toBe('r1')
  })

  it('supports custom key names', () => {
    const storage = createLocalStorageTokenStorage({
      accessTokenKey: 'my_access',
      refreshTokenKey: 'my_refresh',
    })
    storage.setTokens({ accessToken: 'a1', refreshToken: 'r1' })
    expect(localStorage.getItem('my_access')).toBe('a1')
    expect(storage.getAccessToken()).toBe('a1')
  })

  it('omitting refreshToken leaves the stored refresh token untouched', () => {
    const storage = createLocalStorageTokenStorage()
    storage.setTokens({ accessToken: 'a1', refreshToken: 'r1' })
    storage.setTokens({ accessToken: 'a2' })
    expect(storage.getAccessToken()).toBe('a2')
    expect(storage.getRefreshToken()).toBe('r1')
  })

  it('clear() removes both tokens', () => {
    const storage = createLocalStorageTokenStorage()
    storage.setTokens({ accessToken: 'a1', refreshToken: 'r1' })
    storage.clear()
    expect(storage.getAccessToken()).toBeNull()
    expect(storage.getRefreshToken()).toBeNull()
  })
})

describe('createMemoryTokenStorage', () => {
  it('round-trips tokens without touching any global storage', () => {
    const storage = createMemoryTokenStorage()
    expect(storage.getAccessToken()).toBeNull()
    storage.setTokens({ accessToken: 'a1', refreshToken: 'r1' })
    expect(storage.getAccessToken()).toBe('a1')
    expect(storage.getRefreshToken()).toBe('r1')
    storage.clear()
    expect(storage.getAccessToken()).toBeNull()
  })
})
