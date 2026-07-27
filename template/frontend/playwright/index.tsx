import '../src/index.css'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'
import { beforeMount } from '@playwright/experimental-ct-react/hooks'
import { MemoryRouter, Route, Routes } from 'react-router-dom'

// Freeze snapshot backgrounds to an opaque surface so PNGs are stable regardless of
// the host page.
document.documentElement.style.background = '#fff'

export interface HooksConfig {
  /** Initial URL for the MemoryRouter, e.g. `/things/thing-1`. */
  route?: string
  /** Route pattern to mount under when the component reads router params, e.g.
   *  `/things/:thingId`. Omit for components that need no params. */
  path?: string
  /** Fixed width (px) for the snapshot frame. Omit to shrink-wrap the content — set
   *  this for full-width page layouts that would otherwise collapse, and match it to
   *  the viewport when using `expectNoHorizontalOverflow`. */
  width?: number
  /** Drop the white padding frame for full-bleed page layouts (their own background
   *  reaches the viewport edge, so the frame reads as a fake border). */
  bleed?: boolean
}

/**
 * Wrap every mounted component in the app's real provider stack: a fresh QueryClient
 * (no retries, no refetch — a retry would race the screenshot), and a MemoryRouter.
 * Add your theme provider here too; the goal is that a component under test sees the
 * same context it sees in the app, so a snapshot failure means the component changed
 * rather than the harness did.
 *
 * Per-test routing comes from `hooksConfig` passed to `mount(...)`.
 *
 * The `#snapshot-root` wrapper shrink-wraps the component (or takes a fixed `width`)
 * and adds padding so box-shadows and focus rings aren't clipped — `snapshotRoot`
 * screenshots this node.
 */
beforeMount<HooksConfig>(async ({ App, hooksConfig }) => {
  const route = hooksConfig?.route ?? '/'
  const path = hooksConfig?.path
  const width = hooksConfig?.width
  const queryClient = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Number.POSITIVE_INFINITY },
      mutations: { retry: false },
    },
  })
  return (
    <QueryClientProvider client={queryClient}>
      <MemoryRouter initialEntries={[route]}>
        <div
          id="snapshot-root"
          style={{
            display: width ? 'block' : 'inline-block',
            width: width ? `${width}px` : undefined,
            padding: hooksConfig?.bleed ? 0 : 16,
            background: '#fff',
          }}
        >
          {path ? (
            <Routes>
              <Route path={path} element={<App />} />
            </Routes>
          ) : (
            <App />
          )}
        </div>
      </MemoryRouter>
    </QueryClientProvider>
  )
})
