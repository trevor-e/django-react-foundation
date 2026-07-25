import crypto from 'node:crypto'
import { describe, expect, it } from 'vitest'
import { AUTH_GATE_ATTRIBUTE, authGateHeadSnippet, authGateScript } from '../src/authGate'
import { cspScriptHash, patchScriptSrcHash } from '../src/prerender'

describe('authGateScript', () => {
  it('checks the tokenStorage default key by default', () => {
    expect(authGateScript()).toContain(`localStorage.getItem("auth_token")`)
  })

  it('embeds a custom key safely and sets the shared attribute', () => {
    const js = authGateScript(`weird"key`)
    expect(js).toContain(JSON.stringify(`weird"key`))
    expect(js).toContain(`setAttribute('${AUTH_GATE_ATTRIBUTE}','')`)
  })

  it('never touches the token value — presence check only', () => {
    // The gate must stay a pure key-presence probe: no assignment of the
    // getItem result, nothing but the attribute write in the if-body.
    expect(authGateScript()).toMatch(/if\(localStorage\.getItem\("auth_token"\)\)document/)
  })
})

describe('authGateHeadSnippet', () => {
  it('pairs the script with the #root-hiding style on the same attribute', () => {
    const snippet = authGateHeadSnippet()
    expect(snippet).toContain(`<script>${authGateScript()}</script>`)
    expect(snippet).toContain(`<style>html[${AUTH_GATE_ATTRIBUTE}] #root{display:none}</style>`)
  })
})

describe('cspScriptHash / patchScriptSrcHash', () => {
  it('hashes the exact script bytes as CSP expects', () => {
    const js = authGateScript()
    const expected = crypto.createHash('sha256').update(js).digest('base64')
    expect(cspScriptHash(js)).toBe(`sha256-${expected}`)
  })

  it("extends script-src 'self' in place, leaving the rest untouched", () => {
    const headers = `/*\n  Content-Security-Policy: default-src 'self'; script-src 'self' https://x.example; style-src 'self' 'unsafe-inline'\n`
    const patched = patchScriptSrcHash(headers, 'sha256-abc')
    expect(patched).toContain("script-src 'self' 'sha256-abc' https://x.example")
    expect(patched).toContain("style-src 'self' 'unsafe-inline'")
  })

  it('throws when the directive is missing rather than shipping a blocked script', () => {
    expect(() => patchScriptSrcHash("script-src 'none'", 'sha256-abc')).toThrow(/script-src 'self'/)
  })
})
