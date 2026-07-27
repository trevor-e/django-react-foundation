import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiRequestError, createApiClient } from '../src/apiClient'

function jsonResponse(body: unknown, init: { status?: number } = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

/** A stand-in for the app's CSRF store (in adulting: a module-level variable fed by
 * login responses). */
function csrfStore(initial: string | null = null) {
  let token = initial
  return {
    getCsrfToken: () => token,
    setCsrfToken: (value: string) => {
      token = value
    },
    current: () => token,
  }
}

const headersOf = (call: unknown[]) =>
  (call[1] as RequestInit).headers as Record<string, string>

describe('createApiClient — session mode', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('rejects being given both auth modes, or neither', () => {
    expect(() => createApiClient({ baseUrl: 'https://api.test' })).toThrow(/exactly one/)
  })

  it('sends cookies and no Authorization header', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'success', data: { id: 1 } }))
    const client = createApiClient({ baseUrl: 'https://api.test', session: csrfStore('tok') })

    await client.request('/api/x')

    const [, init] = fetchMock.mock.calls[0]
    expect((init as RequestInit).credentials).toBe('include')
    expect(headersOf(fetchMock.mock.calls[0])['Authorization']).toBeUndefined()
  })

  it('does not send a CSRF token on safe methods', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'success', data: null }))
    const client = createApiClient({ baseUrl: 'https://api.test', session: csrfStore('tok') })

    await client.request('/api/x')

    expect(headersOf(fetchMock.mock.calls[0])['X-CSRFToken']).toBeUndefined()
  })

  it('sends the CSRF token on unsafe methods', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'success', data: null }))
    const client = createApiClient({ baseUrl: 'https://api.test', session: csrfStore('tok') })

    await client.request('/api/x', { method: 'POST' })

    expect(headersOf(fetchMock.mock.calls[0])['X-CSRFToken']).toBe('tok')
  })

  it('bootstraps a token before the first mutation when it has none', async () => {
    const store = csrfStore()
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ status: 'success', data: { csrf_token: 'fresh' } }))
      .mockResolvedValueOnce(jsonResponse({ status: 'success', data: null }))
    const client = createApiClient({ baseUrl: 'https://api.test', session: store })

    await client.request('/api/x', { method: 'POST' })

    expect(fetchMock.mock.calls[0][0]).toBe('https://api.test/api/auth/csrf')
    expect(headersOf(fetchMock.mock.calls[1])['X-CSRFToken']).toBe('fresh')
    expect(store.current()).toBe('fresh')
  })

  it('never touches the CSRF endpoint for reads', async () => {
    // The zero-cookie property for anonymous visitors depends on this: a public page
    // that only reads must not trigger a session-creating bootstrap.
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'success', data: null }))
    const client = createApiClient({ baseUrl: 'https://api.test', session: csrfStore() })

    await client.request('/api/public')

    expect(fetchMock).toHaveBeenCalledTimes(1)
    expect(fetchMock.mock.calls[0][0]).toBe('https://api.test/api/public')
  })

  it('re-bootstraps once and retries when the token is stale', async () => {
    const store = csrfStore('stale')
    fetchMock
      .mockResolvedValueOnce(
        jsonResponse({ status: 'error', detail: 'CSRF Failed: token mismatch' }, { status: 403 })
      )
      .mockResolvedValueOnce(jsonResponse({ status: 'success', data: { csrf_token: 'fresh' } }))
      .mockResolvedValueOnce(jsonResponse({ status: 'success', data: { ok: true } }))
    const client = createApiClient({ baseUrl: 'https://api.test', session: store })

    const result = await client.request('/api/x', { method: 'POST' })

    expect(result).toEqual({ ok: true })
    expect(headersOf(fetchMock.mock.calls[2])['X-CSRFToken']).toBe('fresh')
  })

  it('does not retry a 403 that is a genuine permission denial', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ status: 'error', detail: 'You do not have permission.' }, { status: 403 })
    )
    const client = createApiClient({ baseUrl: 'https://api.test', session: csrfStore('tok') })

    await expect(client.request('/api/x', { method: 'POST' })).rejects.toBeInstanceOf(
      ApiRequestError
    )
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('treats a 401 as terminal — no refresh attempt, onAuthFailure fires', async () => {
    fetchMock.mockResolvedValueOnce(
      jsonResponse({ status: 'error', detail: 'Unauthorized' }, { status: 401 })
    )
    const onAuthFailure = vi.fn()
    const client = createApiClient({
      baseUrl: 'https://api.test',
      session: csrfStore('tok'),
      onAuthFailure,
    })

    await expect(client.request('/api/x')).rejects.toThrow(/Session expired/)
    expect(onAuthFailure).toHaveBeenCalledOnce()
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('bootstraps once for concurrent mutations', async () => {
    const store = csrfStore()
    // A fresh Response per call: a body can only be read once, and both requests here
    // land on the same mock.
    fetchMock
      .mockResolvedValueOnce(jsonResponse({ status: 'success', data: { csrf_token: 'fresh' } }))
      .mockImplementation(async () => jsonResponse({ status: 'success', data: null }))
    const client = createApiClient({ baseUrl: 'https://api.test', session: store })

    await Promise.all([
      client.request('/api/a', { method: 'POST' }),
      client.request('/api/b', { method: 'POST' }),
    ])

    const bootstraps = fetchMock.mock.calls.filter(
      ([url]) => url === 'https://api.test/api/auth/csrf'
    )
    expect(bootstraps).toHaveLength(1)
  })
})
