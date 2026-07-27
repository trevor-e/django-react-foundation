import { beforeEach, describe, expect, it, vi } from 'vitest'
import {
  applyTheme,
  getThemePreference,
  setThemePreference,
  THEME_STORAGE_KEY,
} from '../src/theme'

// vitest's default 'node' environment has no DOM; minimal polyfills are enough to
// exercise the storage semantics and the class toggle without adding jsdom.
function install({ prefersDark = false, storageWorks = true } = {}) {
  const store = new Map<string, string>()
  vi.stubGlobal(
    'localStorage',
    storageWorks
      ? {
          getItem: (k: string) => store.get(k) ?? null,
          setItem: (k: string, v: string) => store.set(k, v),
          removeItem: (k: string) => store.delete(k),
        }
      : {
          getItem: () => {
            throw new Error('storage blocked')
          },
          setItem: () => {
            throw new Error('storage blocked')
          },
          removeItem: () => {
            throw new Error('storage blocked')
          },
        },
  )
  const classes = new Set<string>()
  vi.stubGlobal('document', {
    documentElement: {
      classList: {
        toggle: (name: string, on: boolean) => (on ? classes.add(name) : classes.delete(name)),
        add: (name: string) => classes.add(name),
      },
    },
  })
  vi.stubGlobal('window', {
    matchMedia: () => ({
      matches: prefersDark,
      addEventListener: () => {},
      removeEventListener: () => {},
    }),
  })
  return { store, classes }
}

describe('theme preference', () => {
  beforeEach(() => {
    vi.unstubAllGlobals()
  })

  it('defaults to system', () => {
    install()
    expect(getThemePreference()).toBe('system')
  })

  it('stores system as an absent key, so first run needs no write', () => {
    const { store } = install()
    setThemePreference('dark')
    expect(store.get(THEME_STORAGE_KEY)).toBe('dark')
    setThemePreference('system')
    expect(store.has(THEME_STORAGE_KEY)).toBe(false)
  })

  it('round-trips an explicit preference', () => {
    install()
    setThemePreference('light')
    expect(getThemePreference()).toBe('light')
  })

  it('ignores a junk stored value rather than trusting it', () => {
    const { store } = install()
    store.set(THEME_STORAGE_KEY, 'chartreuse')
    expect(getThemePreference()).toBe('system')
  })

  it('follows the OS while in system mode', () => {
    const { classes } = install({ prefersDark: true })
    applyTheme()
    expect(classes.has('dark')).toBe(true)
  })

  it('an explicit preference overrides the OS', () => {
    const { classes } = install({ prefersDark: true })
    setThemePreference('light')
    expect(classes.has('dark')).toBe(false)
  })

  it('applyTheme is idempotent', () => {
    const { classes } = install({ prefersDark: true })
    applyTheme()
    applyTheme()
    expect(classes.has('dark')).toBe(true)
  })

  it('behaves as system when storage throws', () => {
    install({ storageWorks: false, prefersDark: true })
    expect(getThemePreference()).toBe('system')
  })

  it('still applies for this page when the write fails', () => {
    const { classes } = install({ storageWorks: false })
    expect(() => setThemePreference('dark')).not.toThrow()
    expect(classes.has('dark')).toBe(true)
  })
})
