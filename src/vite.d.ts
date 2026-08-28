import type { Plugin } from 'vite'

/**
 * The deployed commit sha, truncated for readability (equality is all that's
 * ever compared): Cloudflare Pages build env → GitHub Actions → local git →
 * `"dev"`.
 */
export declare function resolveBuildId(): string

/**
 * Vite plugin: `define`s `__BUILD_ID__` (read by `react-vite-foundation/skew`'s
 * `BUILD_ID`) and emits `version.json` `{buildId, builtAt}` from the client
 * build; the `--ssr` pass emits nothing. Serve `/version.json` uncached.
 */
export declare function buildIdPlugin(): Plugin
