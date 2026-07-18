import { resolveApiBaseUrl } from 'react-vite-foundation'

// Local dev talks to granian on :8000; deployed builds hit the api. domain
// (blueprint §14). Override either with VITE_API_MODE / VITE_API_PROD_URL.
export const API_BASE_URL = resolveApiBaseUrl({
  mode: import.meta.env.VITE_API_MODE,
  isProd: import.meta.env.PROD,
  prodUrl: import.meta.env.VITE_API_PROD_URL ?? 'https://api.__PROJECT__.example',
  devUrl: 'http://localhost:8000',
})
