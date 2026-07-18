/** Minimal fetch-based SSE reader.
 *
 * Hand-rolled because native `EventSource` cannot send an `Authorization` header and
 * bearer tokens must never go in URLs. Reads until the server closes, the signal
 * aborts, or the network drops — reconnect/backoff policy belongs to the caller,
 * which typically wraps this in a loop that re-fetches a catch-up cursor before each
 * (re)connect and pauses while `document.hidden`.
 */

export interface SseFrame {
  id?: string
  event?: string
  data: string
}

export interface SseHandlers {
  /** Called once the response is open and confirmed to be an event stream. */
  onOpen?: () => void
  onFrame: (frame: SseFrame) => void
}

export async function readEventStream(
  url: string,
  token: string | null,
  signal: AbortSignal,
  handlers: SseHandlers,
): Promise<void> {
  const headers: Record<string, string> = { Accept: 'text/event-stream' }
  if (token) headers['Authorization'] = `Bearer ${token}`

  const response = await fetch(url, { headers, signal })
  if (!response.ok || !response.body) {
    throw new Error(`event stream failed: ${response.status}`)
  }
  if (!response.headers.get('content-type')?.includes('text/event-stream')) {
    throw new Error('event stream failed: not an event stream')
  }
  handlers.onOpen?.()

  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader()
  let buffer = ''
  let frame: SseFrame = { data: '' }
  let hasData = false

  const dispatch = () => {
    if (hasData) handlers.onFrame(frame)
    frame = { data: '' }
    hasData = false
  }

  for (;;) {
    const { value, done } = await reader.read()
    if (done) break
    buffer += value
    let newline: number
    while ((newline = buffer.indexOf('\n')) !== -1) {
      const line = buffer.slice(0, newline).replace(/\r$/, '')
      buffer = buffer.slice(newline + 1)
      if (line === '') {
        dispatch()
      } else if (line.startsWith('id:')) {
        frame.id = line.slice(3).trimStart()
      } else if (line.startsWith('event:')) {
        frame.event = line.slice(6).trimStart()
      } else if (line.startsWith('data:')) {
        frame.data += (hasData ? '\n' : '') + line.slice(5).trimStart()
        hasData = true
      }
      // Lines starting with ':' are comments (our heartbeats) — ignored.
    }
  }
}
