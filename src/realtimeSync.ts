import { createStreamLoop } from './streamLoop'

/** Options for `createRealtimeSync` — the client half of the realtime pattern
 * (`drf_foundation.realtime` is the server half). */
export interface RealtimeSyncOptions {
  /** Absolute URL of the SSE endpoint. */
  streamUrl: string
  /** JWT mode: bearer token for the stream fetch, read fresh per connection attempt.
   * Omit under session-cookie auth and set `credentials: 'include'` instead — the
   * browser attaches the cookie itself and there is no token to read. */
  getToken?: () => string | null
  /** Session mode: `'include'` so the session cookie rides the (cross-origin) stream
   * fetch. Defaults to the fetch default, which sends no cookies cross-origin. */
  credentials?: RequestCredentials
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
 *
 * This is the invalidate-and-refetch flavor; when the server keeps an *ordered*
 * event log, prefer `createCursorSync`, which fetches and applies the items
 * themselves instead of just signalling "something changed".
 */
export function createRealtimeSync(options: RealtimeSyncOptions): RealtimeSync {
  // undefined = never fetched (the first check must not fire onChange).
  let lastHead: string | null | undefined

  return createStreamLoop({
    streamUrl: options.streamUrl,
    getToken: options.getToken,
    credentials: options.credentials,
    minBackoffMs: options.minBackoffMs,
    maxBackoffMs: options.maxBackoffMs,
    doc: options.doc,
    beforeConnect: async () => {
      const head = await options.fetchHead()
      if (lastHead !== undefined && head !== lastHead) options.onChange()
      lastHead = head
    },
    onFrame: (frame) => {
      // Data frames carry a change id; named events (`connected`) don't.
      if (frame.event === undefined && frame.data) {
        lastHead = frame.data
        options.onChange()
      }
    },
  })
}
