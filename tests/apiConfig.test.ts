import { describe, expect, it } from 'vitest'
import { resolveApiBaseUrl } from '../src/apiConfig'

describe('resolveApiBaseUrl', () => {
  const opts = { prodUrl: 'https://api.example.com', devUrl: 'http://localhost:8000' }

  it('uses devUrl when not prod and no mode override', () => {
    expect(resolveApiBaseUrl({ ...opts, isProd: false })).toBe('http://localhost:8000')
  })

  it('uses prodUrl when isProd is true', () => {
    expect(resolveApiBaseUrl({ ...opts, isProd: true })).toBe('https://api.example.com')
  })

  it('an explicit mode overrides isProd', () => {
    expect(resolveApiBaseUrl({ ...opts, isProd: true, mode: 'local' })).toBe(
      'http://localhost:8000'
    )
    expect(resolveApiBaseUrl({ ...opts, isProd: false, mode: 'production' })).toBe(
      'https://api.example.com'
    )
  })
})
