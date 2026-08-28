/**
 * Build-time half of the stale-tab skew guard (`react-vite-foundation/skew`):
 * stamp the build's identity into the bundle and publish it next to it.
 *
 * Node-only — import from `vite.config.ts`, never from app code.
 */
import { execSync } from 'node:child_process'
import type { Plugin } from 'vite'

/**
 * The deployed commit sha, truncated for readability (equality is all that's
 * ever compared): Cloudflare Pages build env → GitHub Actions → local git →
 * `"dev"`.
 */
export function resolveBuildId(): string {
  const fromEnv = process.env.CF_PAGES_COMMIT_SHA ?? process.env.GITHUB_SHA
  if (fromEnv) return fromEnv.slice(0, 12)
  try {
    return execSync('git rev-parse HEAD', { encoding: 'utf8' }).trim().slice(0, 12)
  } catch {
    return 'dev'
  }
}

/**
 * Vite plugin: `define`s `__BUILD_ID__` (read by `react-vite-foundation/skew`'s
 * `BUILD_ID`) and emits `version.json` `{buildId, builtAt}` from the client
 * build. The `--ssr` prerender pass gets the define but emits nothing — an
 * internal artifact must not claim the filename. Remember to serve
 * `/version.json` uncached (e.g. a `_headers` rule `Cache-Control: no-store`
 * on Cloudflare Pages, which picks cache headers by request path).
 */
export function buildIdPlugin(): Plugin {
  const buildId = resolveBuildId()
  let isSsrBuild = false
  return {
    name: 'react-vite-foundation:build-id',
    config: () => ({ define: { __BUILD_ID__: JSON.stringify(buildId) } }),
    configResolved(config) {
      isSsrBuild = Boolean(config.build.ssr)
    },
    generateBundle() {
      if (isSsrBuild) return
      this.emitFile({
        type: 'asset',
        fileName: 'version.json',
        source: `${JSON.stringify({ buildId, builtAt: new Date().toISOString() })}\n`,
      })
    },
  }
}
