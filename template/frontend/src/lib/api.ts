import { createApiClient, createSessionHint } from 'react-vite-foundation'
import { API_BASE_URL } from './config'

/** A non-credential marker of "probably signed in", so first paint can skip a pointless
 * /api/me call for an obvious visitor. Set it after login, clear it on logout/401. */
export const sessionHint = createSessionHint()

/** The CSRF token lives in memory, not storage: it is handed back by every
 * session-starting response and re-fetchable from /api/auth/csrf, so persisting it buys
 * nothing and a stale one across tabs costs a retry. */
let csrfToken: string | null = null

export const apiClient = createApiClient({
  baseUrl: API_BASE_URL,
  // Session mode: the credential is an HttpOnly cookie the browser attaches itself, so
  // page JavaScript — and any XSS — has nothing to read.
  session: {
    getCsrfToken: () => csrfToken,
    setCsrfToken: (token) => {
      csrfToken = token
    },
  },
  onAuthFailure: () => {
    sessionHint.clear()
    csrfToken = null
    window.location.href = '/login'
  },
})

/** Call after any response that starts or rotates a session — login, register, and any
 * endpoint returning `csrf_token` — so the next unsafe request needs no extra round trip. */
export function adoptSession(token: string): void {
  csrfToken = token
  sessionHint.set()
}
