import { afterEach, describe, expect, it, vi } from 'vitest'
import { createRealtimeSync } from '../src/realtimeSync'

/** Minimal stand-in for `document`: visibility state + the one event the sync uses. */
class FakeDoc {
  hidden = false
  private listeners = new Set<() => void>()
  addEventListener(_type: string, fn: () => void) {
    this.listeners.add(fn)
  }
  removeEventListener(_type: string, fn: () => void) {
    this.listeners.delete(fn)
  }
  setHidden(hidden: boolean) {
    this.hidden = hidden
    for (const fn of [...this.listeners]) fn()
  }
}

const asDoc = (d: FakeDoc) => d as unknown as Document

/** A controllable SSE Response: push frames, close, observe the abort signal. */
function sseStream() {
  let controller!: ReadableStreamDefaultController<Uint8Array>
  const body = new ReadableStream<Uint8Array>({
    start(c) {
      controller = c
    },
  })
  const response = new Response(body, {
    status: 200,
    headers: { 'Content-Type': 'text/event-stream' },
  })
  const enc = new TextEncoder()
  return {
    response,
    push(frame: string) {
      controller.enqueue(enc.encode(frame))
    },
    close() {
      try {
        controller.close()
      } catch {
        // already closed/errored
      }
    },
    error(err: Error) {
      try {
        controller.error(err)
      } catch {
        // already closed/errored
      }
    },
  }
}

/** Real fetch rejects the body read when its signal aborts; a canned Response does
 * not, so wire the signal to the stream by hand. */
function abortWired(s: ReturnType<typeof sseStream>, init: RequestInit) {
  ;(init.signal as AbortSignal).addEventListener('abort', () => s.error(new Error('aborted')))
  return Promise.resolve(s.response)
}

afterEach(() => {
  vi.unstubAllGlobals()
  vi.useRealTimers()
})

describe('createRealtimeSync', () => {
  it('fires onChange on data frames but not on named events', async () => {
    const s = sseStream()
    const fetchMock = vi.fn().mockResolvedValue(s.response)
    vi.stubGlobal('fetch', fetchMock)
    const onChange = vi.fn()
    const sync = createRealtimeSync({
      streamUrl: 'https://x/stream',
      fetchHead: vi.fn().mockResolvedValue(null),
      onChange,
      doc: null,
    })
    sync.start()
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    s.push('event: connected\ndata: ok\n\n')
    s.push('data: 41\n\n')
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledTimes(1))
    s.push('id: 42\ndata: 42\n\n')
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledTimes(2))
    sync.stop()
  })

  it('does not fire onChange for the first-ever head check, but does when the head moves across a reconnect', async () => {
    const first = sseStream()
    const second = sseStream()
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(first.response)
      .mockResolvedValueOnce(second.response)
    vi.stubGlobal('fetch', fetchMock)
    const heads = ['5', '7']
    const fetchHead = vi.fn(async () => heads.shift() ?? '7')
    const onChange = vi.fn()
    const sync = createRealtimeSync({
      streamUrl: 'https://x/stream',
      fetchHead,
      onChange,
      doc: null,
      minBackoffMs: 1,
      maxBackoffMs: 2,
    })
    sync.start()
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    expect(onChange).not.toHaveBeenCalled() // first head check must not fire
    first.close() // stream drops -> reconnect -> head moved 5 -> 7
    await vi.waitFor(() => expect(onChange).toHaveBeenCalledTimes(1))
    sync.stop()
  })

  it('holds no connection while hidden and connects on return to foreground', async () => {
    const s = sseStream()
    const fetchMock = vi.fn().mockResolvedValue(s.response)
    vi.stubGlobal('fetch', fetchMock)
    const doc = new FakeDoc()
    doc.hidden = true
    const sync = createRealtimeSync({
      streamUrl: 'https://x/stream',
      fetchHead: vi.fn().mockResolvedValue(null),
      onChange: vi.fn(),
      doc: asDoc(doc),
    })
    sync.start()
    await new Promise((r) => setTimeout(r, 20))
    expect(fetchMock).not.toHaveBeenCalled()
    doc.setHidden(false)
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    sync.stop()
  })

  it('aborts the open stream when hidden and when stopped', async () => {
    const signals: AbortSignal[] = []
    const fetchMock = vi.fn((_url: string, init: RequestInit) => {
      signals.push(init.signal as AbortSignal)
      return abortWired(sseStream(), init)
    })
    vi.stubGlobal('fetch', fetchMock)
    const doc = new FakeDoc()
    const sync = createRealtimeSync({
      streamUrl: 'https://x/stream',
      fetchHead: vi.fn().mockResolvedValue(null),
      onChange: vi.fn(),
      doc: asDoc(doc),
      minBackoffMs: 1,
      maxBackoffMs: 2,
    })
    sync.start()
    await vi.waitFor(() => expect(signals.length).toBe(1))
    doc.setHidden(true)
    expect(signals[0].aborted).toBe(true)
    await new Promise((r) => setTimeout(r, 0)) // let the aborted read settle
    doc.setHidden(false)
    await vi.waitFor(() => expect(signals.length).toBe(2))
    sync.stop()
    expect(signals[1].aborted).toBe(true)
  })

  it('backs off exponentially between failed attempts and caps the delay', async () => {
    vi.useFakeTimers()
    const fetchMock = vi.fn().mockRejectedValue(new Error('network down'))
    vi.stubGlobal('fetch', fetchMock)
    const sync = createRealtimeSync({
      streamUrl: 'https://x/stream',
      fetchHead: vi.fn().mockResolvedValue(null),
      onChange: vi.fn(),
      doc: null,
      minBackoffMs: 4,
      maxBackoffMs: 16,
    })
    sync.start()
    await vi.advanceTimersByTimeAsync(0)
    expect(fetchMock).toHaveBeenCalledTimes(1) // attempt 1 failed; retry in 4ms
    await vi.advanceTimersByTimeAsync(3)
    expect(fetchMock).toHaveBeenCalledTimes(1)
    await vi.advanceTimersByTimeAsync(1)
    expect(fetchMock).toHaveBeenCalledTimes(2) // retry in 8ms
    await vi.advanceTimersByTimeAsync(8)
    expect(fetchMock).toHaveBeenCalledTimes(3) // retry in 16ms (capped)
    await vi.advanceTimersByTimeAsync(16)
    expect(fetchMock).toHaveBeenCalledTimes(4)
    await vi.advanceTimersByTimeAsync(16)
    expect(fetchMock).toHaveBeenCalledTimes(5) // still capped
    sync.stop()
  })

  it('resets the backoff after a successful open', async () => {
    vi.useFakeTimers()
    const good = sseStream()
    const fetchMock = vi
      .fn()
      .mockRejectedValueOnce(new Error('down')) // attempt 1 fails -> retry 4ms
      .mockRejectedValueOnce(new Error('down')) // attempt 2 fails -> retry 8ms
      .mockResolvedValueOnce(good.response) // attempt 3 opens -> attempt counter resets
      .mockRejectedValue(new Error('down'))
    vi.stubGlobal('fetch', fetchMock)
    const sync = createRealtimeSync({
      streamUrl: 'https://x/stream',
      fetchHead: vi.fn().mockResolvedValue(null),
      onChange: vi.fn(),
      doc: null,
      minBackoffMs: 4,
      maxBackoffMs: 64,
    })
    sync.start()
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(4) // attempt 2
    await vi.advanceTimersByTimeAsync(8) // attempt 3 (opens)
    expect(fetchMock).toHaveBeenCalledTimes(3)
    good.close() // drops -> next retry should be back at min (4ms), not 16
    await vi.advanceTimersByTimeAsync(0)
    await vi.advanceTimersByTimeAsync(4)
    expect(fetchMock).toHaveBeenCalledTimes(4)
    sync.stop()
  })
})
