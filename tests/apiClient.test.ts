import { beforeEach, describe, expect, it, vi } from 'vitest'
import { ApiRequestError, createApiClient } from '../src/apiClient'
import { createMemoryTokenStorage } from '../src/tokenStorage'

function jsonResponse(body: unknown, init: { status?: number } = {}) {
  return new Response(JSON.stringify(body), {
    status: init.status ?? 200,
    headers: { 'Content-Type': 'application/json' },
  })
}

describe('createApiClient', () => {
  let fetchMock: ReturnType<typeof vi.fn>

  beforeEach(() => {
    fetchMock = vi.fn()
    vi.stubGlobal('fetch', fetchMock)
  })

  it('unwraps the {status, data} success envelope', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'success', data: { id: 1 } }))
    const client = createApiClient({ baseUrl: 'https://api.test', tokenStorage: createMemoryTokenStorage() })

    const result = await client.request('/api/x')

    expect(result).toEqual({ id: 1 })
  })

  it('tolerates an already-unwrapped body', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ id: 1 }))
    const client = createApiClient({ baseUrl: 'https://api.test', tokenStorage: createMemoryTokenStorage() })

    expect(await client.request('/api/x')).toEqual({ id: 1 })
  })

  it('returns undefined for a 204 without parsing a body', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }))
    const client = createApiClient({ baseUrl: 'https://api.test', tokenStorage: createMemoryTokenStorage() })

    expect(await client.request('/api/x')).toBeUndefined()
  })

  it('attaches a bearer token when one is present', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'success', data: null }))
    const tokenStorage = createMemoryTokenStorage()
    tokenStorage.setTokens({ accessToken: 'token-1' })
    const client = createApiClient({ baseUrl: 'https://api.test', tokenStorage })

    await client.request('/api/x')

    const [, init] = fetchMock.mock.calls[0]
    expect((init.headers as Record<string, string>)['Authorization']).toBe('Bearer token-1')
  })

  it('omits the Authorization header when there is no token', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'success', data: null }))
    const client = createApiClient({ baseUrl: 'https://api.test', tokenStorage: createMemoryTokenStorage() })

    await client.request('/api/x')

    const [, init] = fetchMock.mock.calls[0]
    expect((init.headers as Record<string, string>)['Authorization']).toBeUndefined()
  })

  it('throws ApiRequestError (with status) on a non-ok, non-401 response', async () => {
    fetchMock.mockResolvedValueOnce(new Response('nope', { status: 500, statusText: 'Internal Server Error' }))
    const client = createApiClient({ baseUrl: 'https://api.test', tokenStorage: createMemoryTokenStorage() })

    await expect(client.request('/api/x')).rejects.toMatchObject(
      expect.objectContaining({ status: 500 })
    )
  })

  it('refreshes once and retries on a 401 when a refresh token is available', async () => {
    const tokenStorage = createMemoryTokenStorage()
    tokenStorage.setTokens({ accessToken: 'old-token', refreshToken: 'refresh-1' })

    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 })) // first attempt
      .mockResolvedValueOnce(jsonResponse({ access_token: 'new-token' })) // refresh call
      .mockResolvedValueOnce(jsonResponse({ status: 'success', data: { ok: true } })) // retry

    const client = createApiClient({ baseUrl: 'https://api.test', tokenStorage })
    const result = await client.request('/api/x')

    expect(result).toEqual({ ok: true })
    expect(tokenStorage.getAccessToken()).toBe('new-token')
    expect(fetchMock).toHaveBeenCalledTimes(3)

    const refreshCall = fetchMock.mock.calls[1]
    expect(refreshCall[0]).toBe('https://api.test/api/auth/refresh')

    const retryCall = fetchMock.mock.calls[2]
    expect((retryCall[1].headers as Record<string, string>)['Authorization']).toBe('Bearer new-token')
  })

  it('does not attempt a refresh when there is no refresh token, and surfaces the 401', async () => {
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 401, statusText: 'Unauthorized' }))
    const client = createApiClient({ baseUrl: 'https://api.test', tokenStorage: createMemoryTokenStorage() })

    await expect(client.request('/api/x')).rejects.toBeInstanceOf(ApiRequestError)
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('clears tokens and calls onAuthFailure when the refresh call itself fails', async () => {
    const tokenStorage = createMemoryTokenStorage()
    tokenStorage.setTokens({ accessToken: 'old-token', refreshToken: 'refresh-1' })
    const onAuthFailure = vi.fn()

    fetchMock
      .mockResolvedValueOnce(new Response(null, { status: 401 })) // first attempt
      .mockResolvedValueOnce(new Response(null, { status: 400 })) // refresh call fails

    const client = createApiClient({
      baseUrl: 'https://api.test',
      tokenStorage,
      onAuthFailure,
    })

    await expect(client.request('/api/x')).rejects.toThrow('Session expired')
    expect(tokenStorage.getAccessToken()).toBeNull()
    expect(onAuthFailure).toHaveBeenCalledOnce()
  })

  it('supports a function baseUrl for a runtime-resolved backend', async () => {
    fetchMock.mockResolvedValueOnce(jsonResponse({ status: 'success', data: null }))
    const client = createApiClient({
      baseUrl: () => 'https://dynamic.test',
      tokenStorage: createMemoryTokenStorage(),
    })

    await client.request('/api/x')

    expect(fetchMock.mock.calls[0][0]).toBe('https://dynamic.test/api/x')
  })
})
