import { readEventStream } from './sse'

/** Options for `createRealtimeSync` — the client half of the realtime pattern
 * (`drf_foundation.realtime` is the server half). */
export interface RealtimeSyncOptions {
  /** Absolute URL of the SSE endpoint. */
  streamUrl: string
  /** Bearer token for the stream fetch, read fresh per connection attempt. */
  getToken: () => string | null
  /**
   * Fetch the current change cursor (latest event id, or null when none). Called
   * through the app's refresh-aware API client so it doubles as the token-refresh
   * hook before each stream attempt. While the stream is down, the reconnect loop
   * calls this on every attempt — which IS the polling fallback (bounded by
   * `maxBackoffMs`), no separate poller needed.
   */
  fetchHead: () => Promise<string | null>
  /** React to a change (typically: invalidate the tenant's query-cache prefix). */
  onChange: () => void
  /** Reconnect backoff bounds. Defaults: 2s doubling to 30s. */
  minBackoffMs?: number
  maxBackoffMs?: number
  /** Visibility source; defaults to `document`. Pass null to disable hidden-pausing. */
  doc?: Document | null
}

export interface RealtimeSync {
  start(): void
  stop(): void
}

/**
 * Keep a client session converged with the server via an SSE stream of change ids,
 * with reconnect backoff, hidden-tab pausing, and catch-up on every (re)connect:
 *
 * - each data frame → `onChange()` (invalidate-and-refetch beats cache patching);
 * - before every connect, `fetchHead()` runs and `onChange()` fires if the cursor
 *   moved since last seen — closing gaps from disconnects and hidden pauses;
 * - `document.hidden` aborts the stream (parked tabs hold no connections); return
 *   to foreground reconnects immediately with that same catch-up check.
 */
export function createRealtimeSync(options: RealtimeSyncOptions): RealtimeSync {
  const minBackoff = options.minBackoffMs ?? 2_000
  const maxBackoff = options.maxBackoffMs ?? 30_000
  const doc = options.doc === undefined ? document : options.doc

  let stopped = true
  let connecting = false
  let controller: AbortController | null = null
  let timer: ReturnType<typeof setTimeout> | undefined
  let attempt = 0
  // undefined = never fetched (the first check must not fire onChange).
  let lastHead: string | null | undefined

  const hidden = () => doc?.hidden ?? false

  const checkHead = async () => {
    const head = await options.fetchHead()
    if (lastHead !== undefined && head !== lastHead) options.onChange()
    lastHead = head
  }

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
      await checkHead()
      controller = new AbortController()
      await readEventStream(options.streamUrl, options.getToken(), controller.signal, {
        onOpen: () => {
          attempt = 0
        },
        onFrame: (frame) => {
          // Data frames carry a change id; named events (`connected`) don't.
          if (frame.event === undefined && frame.data) {
            lastHead = frame.data
            options.onChange()
          }
        },
      })
    } catch {
      // Aborted (stop/hidden), auth failure, or network drop — the retry
      // scheduler decides; fetchHead's client already handled token refresh.
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
