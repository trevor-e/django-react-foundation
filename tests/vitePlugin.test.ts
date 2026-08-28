import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { buildIdPlugin, resolveBuildId } from '../src/vite'

// The hooks are exercised directly — spinning a real Vite build to test three
// tiny hooks would drag the whole bundler in as a runtime dev dependency.
type EmittedAsset = { type: string; fileName: string; source: string }

function hooksOf(plugin: ReturnType<typeof buildIdPlugin>) {
  return {
    config: plugin.config as () => { define: Record<string, string> },
    configResolved: plugin.configResolved as (config: { build: { ssr: unknown } }) => void,
    generateBundle: plugin.generateBundle as (this: {
      emitFile: (file: EmittedAsset) => void
    }) => void,
  }
}

const savedEnv: Record<string, string | undefined> = {}

beforeEach(() => {
  for (const key of ['CF_PAGES_COMMIT_SHA', 'GITHUB_SHA']) {
    savedEnv[key] = process.env[key]
    delete process.env[key]
  }
})

afterEach(() => {
  for (const [key, value] of Object.entries(savedEnv)) {
    if (value === undefined) delete process.env[key]
    else process.env[key] = value
  }
  vi.restoreAllMocks()
})

describe('resolveBuildId', () => {
  it('prefers the Pages build env, truncated to 12', () => {
    process.env.CF_PAGES_COMMIT_SHA = 'abcdef0123456789abcdef0123456789abcdef01'
    process.env.GITHUB_SHA = 'ffffffffffffffffffffffffffffffffffffffff'
    expect(resolveBuildId()).toBe('abcdef012345')
  })

  it('falls back to the CI sha', () => {
    process.env.GITHUB_SHA = 'ffffffffffffffffffffffffffffffffffffffff'
    expect(resolveBuildId()).toBe('ffffffffffff')
  })

  it('falls back to git locally', () => {
    // This repo is a git checkout, so the fallback resolves a real sha.
    expect(resolveBuildId()).toMatch(/^[0-9a-f]{12}$/)
  })
})

describe('buildIdPlugin', () => {
  it('defines __BUILD_ID__ and emits a matching version.json from the client build', () => {
    process.env.CF_PAGES_COMMIT_SHA = 'abcdef0123456789abcdef0123456789abcdef01'
    const hooks = hooksOf(buildIdPlugin())

    expect(hooks.config().define.__BUILD_ID__).toBe('"abcdef012345"')

    hooks.configResolved({ build: { ssr: false } })
    const emitted: EmittedAsset[] = []
    hooks.generateBundle.call({ emitFile: (file) => emitted.push(file) })

    expect(emitted).toHaveLength(1)
    expect(emitted[0].fileName).toBe('version.json')
    const body = JSON.parse(emitted[0].source) as { buildId: string; builtAt: string }
    expect(body.buildId).toBe('abcdef012345')
    expect(Number.isNaN(Date.parse(body.builtAt))).toBe(false)
  })

  it('emits nothing from the SSR prerender pass', () => {
    process.env.CF_PAGES_COMMIT_SHA = 'abcdef0123456789abcdef0123456789abcdef01'
    const hooks = hooksOf(buildIdPlugin())

    hooks.configResolved({ build: { ssr: 'src/prerender-entry.tsx' } })
    const emitted: EmittedAsset[] = []
    hooks.generateBundle.call({ emitFile: (file) => emitted.push(file) })

    expect(emitted).toHaveLength(0)
  })
})
