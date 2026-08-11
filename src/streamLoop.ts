import { readEventStream, type SseFrame } from './sse'

/** Internal machinery shared by `createRealtimeSync` and `createCursorSync`: one
 * long-lived SSE connection with reconnect backoff, hidden-tab pausing (parked tabs
 * hold no connections), and a caller-supplied catch-up step before every (re)connect.
 * Not exported from the package root — the two sync flavors are the public API. */
export interface StreamLoopOptions {
  /** Absolute URL of the SSE endpoint. */
  streamUrl: string
  /** JWT mode: bearer token for the stream fetch, read fresh per connection attempt.
   * Omit under session-cookie auth and set `credentials: 'include'` instead. */
  getToken?: () => string | null
  /** Session mode: `'include'` so the session cookie rides the stream fetch. */
  credentials?: RequestCredentials
  /** Awaited before each connection attempt — the catch-up hook (and, while the
   * stream is down, the polling fallback: the reconnect loop calls it on every
   * attempt, bounded by `maxBackoffMs`). A rejection counts as a failed attempt. */
  beforeConnect: () => Promise<void>
  onFrame: (frame: SseFrame) => void
  /** Reconnect backoff bounds. Defaults: 2s doubling to 30s. */
  minBackoffMs?: number
  maxBackoffMs?: number
  /** Visibility source; defaults to `document`. Pass null to disable hidden-pausing. */
  doc?: Document | null
}

export interface StreamLoopHandle {
  start(): void
  stop(): void
}

export function createStreamLoop(options: StreamLoopOptions): StreamLoopHandle {
  const minBackoff = options.minBackoffMs ?? 2_000
  const maxBackoff = options.maxBackoffMs ?? 30_000
  const doc = options.doc === undefined ? document : options.doc

  let stopped = true
  let connecting = false
  let controller: AbortController | null = null
  let timer: ReturnType<typeof setTimeout> | undefined
  let attempt = 0

  const hidden = () => doc?.hidden ?? false

  const scheduleRetry = () => {
    if (stopped || hidden()) return
    attempt += 1
    const delay = Math.min(maxBackoff, minBackoff * 2 ** (attempt - 1))
    timer = setTimeout(connect, delay)
  }

  const connect = async () => {
    if (stopped || hidden() || connecting) return
    connecting = true
    try {
      await options.beforeConnect()
      controller = new AbortController()
      await readEventStream(
        options.streamUrl,
        options.getToken?.() ?? null,
        controller.signal,
        {
          onOpen: () => {
            attempt = 0
          },
          onFrame: options.onFrame,
        },
        { credentials: options.credentials },
      )
    } catch {
      // Aborted (stop/hidden), auth failure, or network drop — the retry
      // scheduler decides; beforeConnect's client already handled token refresh.
    }
    connecting = false
    scheduleRetry()
  }

  const onVisibilityChange = () => {
    if (hidden()) {
      controller?.abort()
      clearTimeout(timer)
    } else {
      clearTimeout(timer)
      attempt = 0
      void connect()
    }
  }

  return {
    start() {
      if (!stopped) return
      stopped = false
      doc?.addEventListener('visibilitychange', onVisibilityChange)
      void connect()
    },
    stop() {
      stopped = true
      doc?.removeEventListener('visibilitychange', onVisibilityChange)
      controller?.abort()
      clearTimeout(timer)
    },
  }
}
