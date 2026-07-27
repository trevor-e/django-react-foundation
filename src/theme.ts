import { useSyncExternalStore } from 'react'

/**
 * Light/dark appearance preference, persisted in localStorage — never a cookie, so a
 * project can hold a no-cookie-banner posture.
 *
 * `system` is the default and is stored as an *absent* key, so first run needs no
 * write and the pre-paint boot snippet (see `template/frontend/public/boot-guard.js`)
 * can treat absence as "follow the OS". That snippet applies the `dark` class before
 * the bundle loads, which is what prevents a flash of the wrong theme; this module
 * owns changes afterwards. **Keep the storage key and the absent-means-system
 * semantics in sync between the two.**
 */
export type ThemePreference = 'light' | 'dark' | 'system'

export const THEME_STORAGE_KEY = 'theme_preference'

const DARK_QUERY = '(prefers-color-scheme: dark)'

const listeners = new Set<() => void>()

export function getThemePreference(): ThemePreference {
  try {
    const stored = localStorage.getItem(THEME_STORAGE_KEY)
    if (stored === 'light' || stored === 'dark') return stored
  } catch {
    // Storage unavailable (private mode, blocked cookies/storage): behave as
    // `system`, matching what the boot snippet does in the same situation.
  }
  return 'system'
}

export function setThemePreference(pref: ThemePreference): void {
  try {
    if (pref === 'system') localStorage.removeItem(THEME_STORAGE_KEY)
    else localStorage.setItem(THEME_STORAGE_KEY, pref)
  } catch {
    // Not persistable, but still apply for this page's lifetime — which is why the
    // intent is handed to applyTheme directly below rather than read back out of
    // storage that just proved unavailable.
  }
  applyTheme(pref)
  for (const notify of listeners) notify()
}

/**
 * Set the `dark` class on <html> to match the effective appearance. Idempotent — safe
 * to call from the matchMedia listener on every OS flip (a no-op unless the preference
 * is `system`, since an explicit preference never consults the media query).
 */
export function applyTheme(pref: ThemePreference = getThemePreference()): void {
  const dark = pref === 'dark' || (pref === 'system' && window.matchMedia(DARK_QUERY).matches)
  document.documentElement.classList.toggle('dark', dark)
}

function subscribe(onStoreChange: () => void): () => void {
  listeners.add(onStoreChange)
  // While in `system`, OS appearance changes must take effect live. Wrapped rather
  // than passed directly: the listener receives a MediaQueryListEvent, which would
  // otherwise land in applyTheme's `pref` parameter. Held in a variable so add and
  // remove see the same reference.
  const onOsChange = () => applyTheme()
  const mq = window.matchMedia(DARK_QUERY)
  mq.addEventListener('change', onOsChange)
  return () => {
    listeners.delete(onStoreChange)
    mq.removeEventListener('change', onOsChange)
  }
}

/** Current preference + setter; re-renders subscribers on any change. */
export function useThemePreference(): [ThemePreference, (pref: ThemePreference) => void] {
  const pref = useSyncExternalStore(subscribe, getThemePreference, () => 'system' as const)
  return [pref, setThemePreference]
}
