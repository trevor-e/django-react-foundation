/**
 * Local/prod API base URL switch.
 *
 * Deliberately takes `mode`/`isProd` as plain arguments instead of reading
 * `import.meta.env` itself — that keeps this function testable without mocking Vite's
 * env, and keeps the package agnostic of any particular bundler's env API. A consuming
 * app's own `config.ts` stays a 3-line call site:
 *
 * ```ts
 * import { resolveApiBaseUrl } from 'react-vite-foundation'
 *
 * export const API_BASE_URL = resolveApiBaseUrl({
 *   mode: import.meta.env.VITE_API_MODE,
 *   isProd: import.meta.env.PROD,
 *   prodUrl: 'https://api.example.com',
 *   devUrl: 'http://localhost:8000',
 * })
 * ```
 */
export interface ApiConfigOptions {
  /** Explicit override, e.g. `VITE_API_MODE=production`. Wins over `isProd` when set. */
  mode?: string
  /** The bundler's own prod/dev flag (e.g. `import.meta.env.PROD`), used when `mode` is unset. */
  isProd: boolean
  prodUrl: string
  devUrl: string
}

export function resolveApiBaseUrl(options: ApiConfigOptions): string {
  const resolvedMode = options.mode || (options.isProd ? 'production' : 'local')
  return resolvedMode === 'production' ? options.prodUrl : options.devUrl
}
