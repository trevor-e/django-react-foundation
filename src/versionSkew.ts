/**
 * Stale-tab skew guard — detect that the running bundle predates the deployed
 * one and reload at moments that cannot destroy the user's work.
 *
 * Why this exists: on this stack a push deploys backend and frontend together,
 * and wire changes are allowed to break compatibility. A tab left open across a
 * deploy keeps running the old bundle against the new API until a changed
 * payload shape crashes it (the class of bug that motivated this: a renamed
 * list field white-screening a day-old tab on its next refetch), or until it
 * quietly renders stale behavior forever.
 *
 * The contract, shared by every consumer:
 *
 *  - Builds embed a build id (the `__BUILD_ID__` define) and publish the same
 *    id at `/version.json` — both come from `buildIdPlugin` in
 *    `react-vite-foundation/vite`. Serve the file uncached: Cloudflare Pages
 *    picks `Cache-Control` by request path, so pin `/version.json` to
 *    `no-store` in the consumer's `_headers`; the fetch here uses `no-store`
 *    too.
 *  - Activity path: mount `VersionSkewWatcher` inside the router. Route
 *    changes and tab-becomes-visible fire a throttled check (one probe per 15
 *    minutes per tab — never a polling timer), and a known-newer build reloads
 *    at the *next* route change: the view was just discarded, so nothing is
 *    lost and the reload lands on the navigated-to URL. Never mid-view.
 *  - Crash path: call `startCrashRecoveryProbe()` from the root error
 *    boundary (an `onError` hook, or a fallback's mount effect). It probes on
 *    a bounded ~2-minute backoff — backend (Railway) and frontend (Pages)
 *    race on every push, so at crash time the new frontend may not be live
 *    yet — and reloads once a newer build appears.
 *  - Reloads are capped at once per distinct deployed build id per tab
 *    (`sessionStorage`, never a cookie), so a crash that survives the reload —
 *    a genuine bug, or a cache pinned on the old bundle — lands on the error
 *    screen instead of looping.
 *
 * Fail-open everywhere: a missing or unparseable version document (a dev
 * server answering the SPA HTML fallback, an offline tab, mid-deploy
 * weirdness) means "no signal", never a reload.
 */
import { useEffect, useRef } from 'react'
import { useLocation } from 'react-router-dom'

declare const __BUILD_ID__: string

/** The running bundle's build id (Vite `define`s it; other tooling gets 'dev'). */
export const BUILD_ID: string = typeof __BUILD_ID__ === 'undefined' ? 'dev' : __BUILD_ID__

const CHECK_INTERVAL_MS = 15 * 60_000
const RELOADED_KEY = 'skewReloadedFor'
const CRASH_PROBE_GAPS_MS = [0, 10_000, 20_000, 30_000, 60_000]

let deployedBuildId: string | null = null
let lastCheckAt = 0
let checkInFlight: Promise<unknown> | null = null
let probing = false

/** The deployed build id, when it's known and isn't the one we're running. */
export function newerBuildId(): string | null {
  return deployedBuildId && deployedBuildId !== BUILD_ID ? deployedBuildId : null
}

/** One unthrottled probe (the crash path's primitive). Resolves to `newerBuildId()`. */
export async function checkForNewerBuild(): Promise<string | null> {
  try {
    const response = await fetch('/version.json', { cache: 'no-store' })
    if (response.ok) {
      const body = (await response.json()) as { buildId?: unknown } | null
      const id = body?.buildId
      if (typeof id === 'string' && id) deployedBuildId = id
    }
  } catch {
    // No signal — see the module docstring.
  }
  return newerBuildId()
}

/** The activity path's primitive: at most one probe per interval, per tab. */
export function maybeCheckForNewerBuild(): void {
  if (checkInFlight || Date.now() - lastCheckAt < CHECK_INTERVAL_MS) return
  lastCheckAt = Date.now()
  checkInFlight = checkForNewerBuild().finally(() => {
    checkInFlight = null
  })
}

/**
 * Reload into the deployed build — at most once per distinct deployed build id
 * per tab (uncapped when storage is unavailable, mirroring how boot-time
 * guards degrade). Returns whether a reload was initiated.
 */
export function reloadForNewBuild(): boolean {
  const target = newerBuildId()
  if (!target) return false
  try {
    if (sessionStorage.getItem(RELOADED_KEY) === target) return false
    sessionStorage.setItem(RELOADED_KEY, target)
  } catch {
    // Storage unavailable: reload anyway, uncapped.
  }
  window.location.reload()
  return true
}

function probe(attempt: number): void {
  window.setTimeout(() => {
    void checkForNewerBuild().then((newer) => {
      if (newer) {
        // Reload, or — when this build id already got its one reload — leave
        // the error screen standing. Either way this loop is done.
        reloadForNewBuild()
        probing = false
        return
      }
      if (attempt + 1 < CRASH_PROBE_GAPS_MS.length) probe(attempt + 1)
      else probing = false
    })
  }, CRASH_PROBE_GAPS_MS[attempt])
}

/**
 * Crash-time recovery: call from the root error boundary. Idempotent while a
 * loop is running (repeat catches and StrictMode double-invokes share one
 * loop); a later, separate crash may start a new bounded loop — the
 * per-build-id reload cap keeps reload loops impossible either way.
 */
export function startCrashRecoveryProbe(): void {
  if (probing) return
  probing = true
  probe(0)
}

/**
 * The activity path as a drop-in component: mount once, inside the router.
 * Detects on activity, acts on navigation; never checks at initial mount (a
 * fresh boot runs the bundle the origin just served), never reloads mid-view.
 */
export function VersionSkewWatcher(): null {
  const { pathname } = useLocation()
  const booting = useRef(true)
  useEffect(() => {
    if (booting.current) {
      booting.current = false
      return
    }
    // Reload if a check already found a newer build (no-op when capped for
    // this build id); otherwise maybe start the next throttled check.
    if (!reloadForNewBuild()) maybeCheckForNewerBuild()
  }, [pathname])
  useEffect(() => {
    const onVisibilityChange = () => {
      if (document.visibilityState === 'visible') maybeCheckForNewerBuild()
    }
    document.addEventListener('visibilitychange', onVisibilityChange)
    return () => document.removeEventListener('visibilitychange', onVisibilityChange)
  }, [])
  return null
}
