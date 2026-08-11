import { createStreamLoop } from './streamLoop'

/** Options for `createCursorSync` — the ordered-log flavor of realtime sync
 * (`drf_foundation.event_log` is the server half). */
export interface CursorSyncOptions<TItem> {
  /** Absolute URL of the SSE endpoint (a seq doorbell — frames carry the head seq). */
  streamUrl: string
  /** JWT mode: bearer token for the stream fetch, read fresh per connection attempt.
   * Omit under session-cookie auth and set `credentials: 'include'` instead. */
  getToken?: () => string | null
  /** Session mode: `'include'` so the session cookie rides the stream fetch. */
  credentials?: RequestCredentials
  /** Fetch one page of items with seq strictly after `cursor`, in ascending order
   * (the server's `events?after=` endpoint). The pump repeats until an empty page. */
  fetchAfter: (cursor: number) => Promise<TItem[]>
  /** Apply an in-order, gap-free batch. MUST advance `getCursor()` past the applied
   * items (the store folds them and records the last seq) — the pump guards against
   * a non-advancing apply to avoid refetch loops, but that guard firing is a bug. */
  apply: (items: TItem[]) => void
  /** The store's current cursor: seq of the last applied item, 0 when none. */
  getCursor: () => number
  /** Backoff bounds for reconnects and failed-pump retries. Defaults: 2s doubling
   * to 30s. */
  minBackoffMs?: number
  maxBackoffMs?: number
  /** Visibility source; defaults to `document`. Pass null to disable hidden-pausing. */
  doc?: Document | null
}

export interface CursorSync {
  start(): void
  stop(): void
  /** Run a catch-up pump now (e.g. after a 409 told you you're stale). Single-flight:
   * concurrent calls coalesce into one extra pass. */
  pump(): Promise<void>
}

/**
 * Keep an ordered local projection converged with a server-side event log:
 * exactly-once, in-order delivery with trivial resume (architecture: SSE is a
 * doorbell, the log is the mailbox).
 *
 * - On every doorbell frame and every (re)connect: fetch items after the local
 *   cursor and apply them in order, repeating until caught up (an empty page).
 * - Doorbells numerically ≤ the local cursor are skipped (stale — the actor already
 *   applied its own command's events from the command response). Correctness never
 *   depends on frame content: every reconnect pumps unconditionally.
 * - Pumps are single-flight; a doorbell landing mid-pump schedules exactly one
 *   follow-up pass. A doorbell pump whose fetch fails retries on the backoff
 *   schedule — on a quiet log the next doorbell may be arbitrarily far away, so
 *   one failed fetch must not strand the client until then.
 * - Reconnect backoff, hidden-tab pausing, and catch-up-on-connect are shared with
 *   `createRealtimeSync` (this is the same loop, one level up).
 */
export function createCursorSync<TItem>(options: CursorSyncOptions<TItem>): CursorSync {
  const minBackoff = options.minBackoffMs ?? 2_000
  const maxBackoff = options.maxBackoffMs ?? 30_000
  const doc = options.doc === undefined ? document : options.doc

  let current: Promise<void> | null = null
  let dirty = false
  let active = false
  let retryTimer: ReturnType<typeof setTimeout> | undefined
  let retryAttempt = 0

  const pump = (): Promise<void> => {
    if (current) {
      // Coalesce: mark the running cycle dirty (it will do one more pass) and let
      // the caller await THAT cycle — it covers their work.
      dirty = true
      return current
    }
    current = (async () => {
      try {
        do {
          dirty = false
          for (;;) {
            const before = options.getCursor()
            const items = await options.fetchAfter(before)
            if (items.length === 0) break
            options.apply(items)
            if (options.getCursor() <= before) break // apply contract violated; don't spin
          }
        } while (dirty)
      } finally {
        current = null
      }
    })()
    return current
  }

  const loop = createStreamLoop({
    streamUrl: options.streamUrl,
    getToken: options.getToken,
    credentials: options.credentials,
    minBackoffMs: options.minBackoffMs,
    maxBackoffMs: options.maxBackoffMs,
    doc: options.doc,
    beforeConnect: pump,
    onFrame: (frame) => {
      // Data frames carry the head seq; named events (`connected`) don't.
      if (frame.event !== undefined || !frame.data) return
      const head = Number(frame.data)
      if (Number.isFinite(head) && head <= options.getCursor()) return // stale doorbell
      void pump().catch(() => {
        // Fetch failed while the stream is healthy — the next doorbell (or the
        // reconnect loop, if the stream drops too) retries.
      })
    },
  })

  return { start: loop.start, stop: loop.stop, pump }
}
