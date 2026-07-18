import { useQuery } from '@tanstack/react-query'
import { apiClient } from './lib/api'

interface HealthCheck {
  status: string
}

/** Starter page proving the wiring: frontend → apiClient → backend envelope.
 * Replace with your routes; the auth endpoints (register/login/refresh/me) are
 * already live on the backend and the apiClient handles tokens once you build
 * a login form against them. */
export default function App() {
  const health = useQuery({
    queryKey: ['health'],
    queryFn: () => apiClient.request<HealthCheck>('/api/health'),
  })

  return (
    <main style={{ fontFamily: 'system-ui', padding: '4rem', textAlign: 'center' }}>
      <h1>__PROJECT__</h1>
      <p>
        Backend:{' '}
        {health.isLoading ? 'checking…' : health.data?.status === 'ok' ? '✅ up' : '❌ unreachable'}
      </p>
    </main>
  )
}
