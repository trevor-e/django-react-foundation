import { afterEach, describe, expect, it, vi } from 'vitest'
import { createCursorSync } from '../src/cursorSync'

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
        /* already closed */
      }
    },
    error(err: Error) {
      try {
        controller.error(err)
      } catch {
        /* already closed */
      }
    },
  }
}

interface Item {
  seq: number
  v: string
}

/** An in-memory server log + client store pair wired the way a real app would be. */
function makeWorld(pageSize = 100) {
  const server: Item[] = []
  const applied: string[] = []
  let cursor = 0
  let inFlight = 0
  let maxInFlight = 0
  const fetchAfter = vi.fn(async (after: number) => {
    inFlight += 1
    maxInFlight = Math.max(maxInFlight, inFlight)
    await Promise.resolve()
    inFlight -= 1
    return server.filter((e) => e.seq > after).slice(0, pageSize)
  })
  return {
    server,
    applied,
    fetchAfter,
    apply: (items: Item[]) => {
      for (const item of items) {
        applied.push(item.v)
        cursor = item.seq
      }
    },
    getCursor: () => cursor,
    push: (v: string) => {
      server.push({ seq: server.length + 1, v })
    },
    maxInFlight: () => maxInFlight,
  }
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('createCursorSync', () => {
  it('catches up on connect, then pumps on doorbells, in order', async () => {
    const s = sseStream()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(s.response))
    const world = makeWorld()
    world.push('a')
    world.push('b')
    const sync = createCursorSync({ streamUrl: 'https://x/stream', ...world, doc: null })
    sync.start()
    await vi.waitFor(() => expect(world.applied).toEqual(['a', 'b'])) // connect catch-up
    world.push('c')
    s.push('event: connected\ndata: ok\n\n')
    s.push('id: 3\ndata: 3\n\n')
    await vi.waitFor(() => expect(world.applied).toEqual(['a', 'b', 'c']))
    sync.stop()
  })

  it('keeps fetching pages until an empty one', async () => {
    const s = sseStream()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(s.response))
    const world = makeWorld(2)
    for (const v of ['a', 'b', 'c', 'd', 'e']) world.push(v)
    const sync = createCursorSync({ streamUrl: 'https://x/stream', ...world, doc: null })
    sync.start()
    await vi.waitFor(() => expect(world.applied).toEqual(['a', 'b', 'c', 'd', 'e']))
    // 2 + 2 + 1 + empty = 4 fetches for the connect pump
    expect(world.fetchAfter.mock.calls.map(([after]) => after)).toEqual([0, 2, 4, 5])
    sync.stop()
  })

  it('skips doorbells at or below the local cursor', async () => {
    const s = sseStream()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(s.response))
    const world = makeWorld()
    world.push('a')
    world.push('b')
    const sync = createCursorSync({ streamUrl: 'https://x/stream', ...world, doc: null })
    sync.start()
    await vi.waitFor(() => expect(world.applied).toEqual(['a', 'b']))
    const fetches = world.fetchAfter.mock.calls.length
    s.push('data: 2\n\n') // the actor's own echo — already applied
    s.push('data: 1\n\n') // ancient
    await new Promise((r) => setTimeout(r, 30))
    expect(world.fetchAfter.mock.calls.length).toBe(fetches)
    sync.stop()
  })

  it('pumps single-flight and coalesces doorbells that land mid-pump', async () => {
    const s = sseStream()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(s.response))
    const world = makeWorld()
    // Gate the first post-connect fetch so a second doorbell lands mid-pump.
    let release!: () => void
    const gate = new Promise<void>((r) => {
      release = r
    })
    const gated = vi.fn(async (after: number) => {
      if (gated.mock.calls.length === 2) await gate // second call = first doorbell pump
      return world.server.filter((e) => e.seq > after).slice(0, 100)
    })
    const sync = createCursorSync({
      streamUrl: 'https://x/stream',
      fetchAfter: gated,
      apply: world.apply,
      getCursor: world.getCursor,
      doc: null,
    })
    sync.start()
    await vi.waitFor(() => expect(gated).toHaveBeenCalledTimes(1)) // connect pump (empty)
    world.push('a')
    s.push('data: 1\n\n') // pump starts, blocks on the gate
    await vi.waitFor(() => expect(gated).toHaveBeenCalledTimes(2))
    world.push('b')
    s.push('data: 2\n\n') // mid-pump doorbell -> dirty, no concurrent fetch
    await new Promise((r) => setTimeout(r, 20))
    expect(gated).toHaveBeenCalledTimes(2) // still just the blocked one
    release()
    await vi.waitFor(() => expect(world.applied).toEqual(['a', 'b']))
    sync.stop()
  })

  it('catches up after a hidden interval without any doorbell', async () => {
    const streams = [sseStream(), sseStream()]
    let calls = 0
    const fetchMock = vi.fn((_url: string, init: RequestInit) => {
      const s = streams[calls++]
      ;(init.signal as AbortSignal).addEventListener('abort', () => s.error(new Error('aborted')))
      return Promise.resolve(s.response)
    })
    vi.stubGlobal('fetch', fetchMock)
    const world = makeWorld()
    const doc = new FakeDoc()
    const sync = createCursorSync({
      streamUrl: 'https://x/stream',
      ...world,
      doc: asDoc(doc),
      minBackoffMs: 1,
      maxBackoffMs: 2,
    })
    sync.start()
    await vi.waitFor(() => expect(fetchMock).toHaveBeenCalledTimes(1))
    doc.setHidden(true)
    await new Promise((r) => setTimeout(r, 0))
    world.push('missed-1')
    world.push('missed-2')
    doc.setHidden(false)
    await vi.waitFor(() => expect(world.applied).toEqual(['missed-1', 'missed-2']))
    sync.stop()
  })

  it('exposes pump() for manual catch-up (the 409 path)', async () => {
    const s = sseStream()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(s.response))
    const world = makeWorld()
    const sync = createCursorSync({ streamUrl: 'https://x/stream', ...world, doc: null })
    sync.start()
    await vi.waitFor(() => expect(world.fetchAfter).toHaveBeenCalled())
    world.push('a')
    await sync.pump()
    expect(world.applied).toEqual(['a'])
    sync.stop()
  })

  it('does not spin when apply fails to advance the cursor', async () => {
    const s = sseStream()
    vi.stubGlobal('fetch', vi.fn().mockResolvedValue(s.response))
    const world = makeWorld()
    world.push('a')
    const sync = createCursorSync({
      streamUrl: 'https://x/stream',
      fetchAfter: world.fetchAfter,
      apply: () => {}, // broken store: never advances
      getCursor: world.getCursor,
      doc: null,
    })
    sync.start()
    await vi.waitFor(() => expect(world.fetchAfter).toHaveBeenCalledTimes(1))
    await new Promise((r) => setTimeout(r, 30))
    expect(world.fetchAfter.mock.calls.length).toBe(1) // guard broke the loop
    sync.stop()
  })
})
