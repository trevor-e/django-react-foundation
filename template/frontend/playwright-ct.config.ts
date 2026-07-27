import path from 'node:path'
import { fileURLToPath } from 'node:url'
import { defineConfig, devices } from '@playwright/experimental-ct-react'

const dirname = path.dirname(fileURLToPath(import.meta.url))

/**
 * Playwright Component Testing config that renders each component to a PNG for a
 * visual-regression service (Sentry Snapshots, Chromatic, Percy — whichever you use).
 *
 * The service owns the diffing, so this deliberately does NOT use Playwright's own
 * `toHaveScreenshot` baseline comparison: the `*.snapshots.tsx` files write PNGs into
 * `.artifacts/snapshots/` via `screenshot({ path })`, and CI uploads that directory.
 * Keeping baselines out of the repo is the point — binary baselines rot, and a hosted
 * differ gives you a review UI.
 *
 * NOTE on JSX: CT auto-injects `@vitejs/plugin-react` only when `ctViteConfig` supplies
 * *zero* plugins (see @playwright/experimental-ct-core's createConfig). The moment you
 * add one — Tailwind, SVGR, anything — that auto-injection is suppressed, JSX compiles
 * to classic `React.createElement`, and every render throws "React is not defined".
 * Setting esbuild's JSX mode explicitly makes this immune to that, whether or not you
 * add plugins later. Leave it in place.
 *
 * Determinism knobs matter more than usual here: a floating locale, timezone, or color
 * scheme turns every snapshot into a false positive on someone else's machine.
 */
export default defineConfig({
  testDir: './src',
  testMatch: /.*\.snapshots\.tsx/,
  retries: 0,
  fullyParallel: true,
  reporter: process.env.CI ? 'line' : 'list',
  use: {
    ctViteConfig: {
      resolve: { alias: { '@': path.resolve(dirname, './src') } },
      esbuild: { jsx: 'automatic', jsxImportSource: 'react' },
    },
    trace: 'off',
    // Pinned so a snapshot taken on a laptop matches one taken in CI.
    colorScheme: 'light',
    timezoneId: 'America/New_York',
    locale: 'en-US',
    viewport: { width: 900, height: 700 },
    deviceScaleFactor: 2,
  },
  projects: [{ name: 'chromium', use: { ...devices['Desktop Chrome'] } }],
})
