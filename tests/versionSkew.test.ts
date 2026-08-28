import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

// vitest's default 'node' environment has none of the browser globals the skew
// guard touches; minimal stubs are enough to exercise the mechanics without
// jsdom — and they make the reload itself directly assertable, which jsdom's
// [Unforgeable] location never allows. `window.setTimeout` forwards late so
// fake timers apply.
type SkewModule = typeof import('../src/versionSkew')

let fetchMock: ReturnType<typeof vi.fn>
let reloadSpy: ReturnType<typeof vi.fn>
let storageBroken: boolean

function installBrowserStubs() {
  const store = new Map<string, string>()
  storageBroken = false
  reloadSpy = vi.fn()
  fetchMock = vi.fn()
  vi.stubGlobal('fetch', fetchMock)
  vi.stubGlobal('sessionStorage', {
    getItem: (key: string) => {
      if (storageBroken) throw new Error('storage denied')
      return store.get(key) ?? null
    },
    setItem: (key: string, value: string) => {
      if (storageBroken) throw new Error('storage denied')
      store.set(key, value)
    },
  })
  vi.stubGlobal('window', {
    setTimeout: (fn: () => void, ms?: number) => setTimeout(fn, ms),
    location: { reload: reloadSpy },
  })
}

const storedGuard = () => (globalThis as { sessionStorage: Storage }).sessionStorage.getItem('skewReloadedFor')

const version = (buildId: unknown): Response =>
  ({ ok: true, json: async () => ({ buildId }) }) as unknown as Response
/** What a dev server or the Pages SPA fallback answers: 200, but not JSON. */
const htmlFallback = (): Response =>
  ({
    ok: true,
    json: async () => {
      throw new SyntaxError('Unexpected token <')
    },
  }) as unknown as Response
const serverError = (): Response => ({ ok: false, json: async () => ({}) }) as unknown as Response

// Module state (known deployed id, throttle clock, probe loop) is module-scoped
// on purpose — so every test imports a fresh copy.
async function loadModule(): Promise<SkewModule> {
  vi.resetModules()
  return import('../src/versionSkew')
}

beforeEach(() => {
  vi.useFakeTimers()
  installBrowserStubs()
})

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('checkForNewerBuild', () => {
  it('detects a newer deployed build', async () => {
    const skew = await loadModule()
    fetchMock.mockResolvedValue(version('deploy-2'))
    expect(await skew.checkForNewerBuild()).toBe('deploy-2')
    expect(skew.newerBuildId()).toBe('deploy-2')
    expect(fetchMock).toHaveBeenCalledWith('/version.json', { cache: 'no-store' })
  })

  it('sees its own build id as "no skew"', async () => {
    const skew = await loadModule()
    fetchMock.mockResolvedValue(version(skew.BUILD_ID))
    expect(await skew.checkForNewerBuild()).toBeNull()
    expect(skew.newerBuildId()).toBeNull()
  })

  it.each([
    ['network failure', () => Promise.reject(new TypeError('offline'))],
    ['HTML fallback body', () => Promise.resolve(htmlFallback())],
    ['non-2xx response', () => Promise.resolve(serverError())],
    ['non-string buildId', () => Promise.resolve(version(42))],
    ['empty buildId', () => Promise.resolve(version(''))],
  ])('fails open on %s', async (_label, respond) => {
    const skew = await loadModule()
    fetchMock.mockImplementation(respond)
    expect(await skew.checkForNewerBuild()).toBeNull()
    expect(skew.newerBuildId()).toBeNull()
    expect(reloadSpy).not.toHaveBeenCalled()
  })
})

describe('maybeCheckForNewerBuild (activity path)', () => {
  it('throttles to one probe per interval', async () => {
    const skew = await loadModule()
    fetchMock.mockResolvedValue(version(skew.BUILD_ID))

    skew.maybeCheckForNewerBuild()
    skew.maybeCheckForNewerBuild()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // Rapid activity within the window stays silent…
    await vi.advanceTimersByTimeAsync(14 * 60_000)
    skew.maybeCheckForNewerBuild()
    expect(fetchMock).toHaveBeenCalledTimes(1)

    // …and the next interval's first activity probes again.
    await vi.advanceTimersByTimeAsync(61_000)
    skew.maybeCheckForNewerBuild()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(2)
  })
})

describe('reloadForNewBuild', () => {
  it('reloads at most once per distinct deployed build id', async () => {
    const skew = await loadModule()
    fetchMock.mockResolvedValue(version('deploy-2'))
    await skew.checkForNewerBuild()

    expect(skew.reloadForNewBuild()).toBe(true)
    expect(reloadSpy).toHaveBeenCalledTimes(1)
    expect(storedGuard()).toBe('deploy-2')
    // Same deployed id again (e.g. the reload landed on a pinned old bundle):
    // capped, the error screen stands.
    expect(skew.reloadForNewBuild()).toBe(false)
    expect(reloadSpy).toHaveBeenCalledTimes(1)

    // A further deploy is a new id and gets its one reload.
    fetchMock.mockResolvedValue(version('deploy-3'))
    await skew.checkForNewerBuild()
    expect(skew.reloadForNewBuild()).toBe(true)
    expect(reloadSpy).toHaveBeenCalledTimes(2)
  })

  it('is a no-op without a known newer build', async () => {
    const skew = await loadModule()
    expect(skew.reloadForNewBuild()).toBe(false)
    expect(reloadSpy).not.toHaveBeenCalled()
  })

  it('still reloads (uncapped) when storage is unavailable', async () => {
    const skew = await loadModule()
    fetchMock.mockResolvedValue(version('deploy-2'))
    await skew.checkForNewerBuild()
    storageBroken = true
    expect(skew.reloadForNewBuild()).toBe(true)
    expect(skew.reloadForNewBuild()).toBe(true)
    expect(reloadSpy).toHaveBeenCalledTimes(2)
  })
})

describe('startCrashRecoveryProbe (crash path)', () => {
  it('keeps probing on the backoff and reloads when skew appears late', async () => {
    const skew = await loadModule()
    fetchMock
      .mockResolvedValueOnce(version(skew.BUILD_ID)) // t=0: frontend deploy not settled
      .mockResolvedValueOnce(version(skew.BUILD_ID)) // t=10s: still not
      .mockResolvedValue(version('deploy-2')) // t=30s: settled

    skew.startCrashRecoveryProbe()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(reloadSpy).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(10_000)
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(reloadSpy).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(20_000)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    expect(reloadSpy).toHaveBeenCalledTimes(1)
    expect(storedGuard()).toBe('deploy-2')

    // The loop ended with the reload — no further probes.
    await vi.advanceTimersByTimeAsync(10 * 60_000)
    expect(fetchMock).toHaveBeenCalledTimes(3)
  })

  it('gives up after the bounded budget when no newer build appears', async () => {
    const skew = await loadModule()
    fetchMock.mockResolvedValue(version(skew.BUILD_ID))

    skew.startCrashRecoveryProbe()
    await vi.advanceTimersByTimeAsync(3 * 60_000)
    expect(fetchMock).toHaveBeenCalledTimes(5) // gaps 0/10/20/30/60s, then stop
    expect(reloadSpy).not.toHaveBeenCalled()

    await vi.advanceTimersByTimeAsync(10 * 60_000)
    expect(fetchMock).toHaveBeenCalledTimes(5)
  })

  it('shares one loop across repeat catches, and a later crash may probe again', async () => {
    const skew = await loadModule()
    fetchMock.mockResolvedValue(version(skew.BUILD_ID))

    skew.startCrashRecoveryProbe()
    skew.startCrashRecoveryProbe() // StrictMode double-invoke / recurring catch
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1)

    await vi.advanceTimersByTimeAsync(3 * 60_000)
    expect(fetchMock).toHaveBeenCalledTimes(5)
    skew.startCrashRecoveryProbe()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(6)
  })
})
