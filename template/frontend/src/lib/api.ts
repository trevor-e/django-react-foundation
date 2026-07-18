import { createApiClient, createLocalStorageTokenStorage } from 'react-vite-foundation'
import { API_BASE_URL } from './config'

export const tokenStorage = createLocalStorageTokenStorage()

export const apiClient = createApiClient({
  baseUrl: API_BASE_URL,
  tokenStorage,
  onAuthFailure: () => {
    window.location.href = '/login'
  },
})
