export { resolveApiBaseUrl } from './apiConfig'
export type { ApiConfigOptions } from './apiConfig'

export {
  createLocalStorageTokenStorage,
  createMemoryTokenStorage,
  createSessionHint,
  DEFAULT_ACCESS_TOKEN_KEY,
  DEFAULT_SESSION_HINT_KEY,
} from './tokenStorage'
export type { TokenStorage, LocalStorageTokenKeys, SessionHint } from './tokenStorage'

export { liftAuthGate, AUTH_GATE_ATTRIBUTE } from './authGate'

export { createApiClient, ApiRequestError } from './apiClient'
export type {
  ApiClient,
  ApiClientOptions,
  RefreshResponse,
  SessionAuthOptions,
} from './apiClient'

export { createQueryKeyFactory } from './queryKeys'

export {
  applyTheme,
  getThemePreference,
  setThemePreference,
  useThemePreference,
  THEME_STORAGE_KEY,
} from './theme'
export type { ThemePreference } from './theme'

export { readEventStream } from './sse'
export type { SseFrame, SseHandlers } from './sse'
export { createRealtimeSync } from './realtimeSync'
export type { RealtimeSync, RealtimeSyncOptions } from './realtimeSync'
