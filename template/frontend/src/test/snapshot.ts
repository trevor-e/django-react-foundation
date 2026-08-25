import { expect, type Locator, type Page } from '@playwright/test'

/** Directory the CI upload step ships to the visual-diff service. */
export const SNAP_DIR = '.artifacts/snapshots'

export type RouteValue =
  | unknown
  | ((url: string, method: string, body: string | null) => unknown)

/**
 * Intercept the app's API calls with a route table. Keys are `'/path'` (GET) or
 * `'METHOD /path'`, substring-matched against the request URL, **longest key first**
 * so `/tasks/task_01h4…` wins over `/tasks`. Values are JSON fixtures or
 * `(url, method, body) => value`.
 *
 * Unmatched `/api/` requests are fulfilled with 404 rather than left hanging, so a
 * missing fixture shows up as an empty/error state in the PNG instead of a timeout
 * you have to go diagnose.
 *
 * Must be called *before* `mount(...)` so routes are live when queries fire.
 */
export async function mockApi(page: Page, routes: Record<string, RouteValue>) {
  const keys = Object.keys(routes).sort((a, b) => b.length - a.length)
  await page.route('**/api/**', async (route) => {
    const request = route.request()
    const url = request.url()
    const method = request.method().toUpperCase()
    const key = keys.find((k) => {
      const space = k.indexOf(' ')
      if (space === -1) return method === 'GET' && url.includes(k)
      return method === k.slice(0, space).toUpperCase() && url.includes(k.slice(space + 1))
    })
    if (key === undefined) {
      await route.fulfill({ status: 404, contentType: 'application/json', body: '{}' })
      return
    }
    const value = routes[key]
    const result =
      typeof value === 'function'
        ? await (value as (u: string, m: string, b: string | null) => unknown)(
            url,
            method,
            request.postData(),
          )
        : value
    await route.fulfill({
      status: 200,
      contentType: 'application/json',
      body: JSON.stringify(result ?? null),
    })
  })
}

/**
 * Set the session hint a route guard checks. Under session-cookie auth there is no
 * readable credential to fake — the cookie is `HttpOnly` — so what a guard can check on
 * the client is the hint, and that is what this sets. The CT page is already loaded when
 * the test body runs, so set it on the live page (mount renders into that same page
 * without navigating, so it is present at render time) *and* via an init script in case
 * a component triggers a reload. Call before `mount`.
 */
export async function loginTestUser(page: Page) {
  // Matches DEFAULT_SESSION_HINT_KEY in react-vite-foundation.
  const setHint = () => {
    window.localStorage.setItem('session_hint', '1')
  }
  await page.addInitScript(setHint)
  await page.evaluate(setHint)
}

/** Capture a specific locator (or the whole page, for portalled dialogs). */
export async function snapshot(target: Locator | Page, slug: string) {
  await target.screenshot({ path: `${SNAP_DIR}/${slug}.png` })
}

/** Capture the `#snapshot-root` wrapper — the shrink-wrapped component frame set up
 *  in `playwright/index.tsx`. The default for in-flow components. */
export async function snapshotRoot(page: Page, slug: string) {
  await page.locator('#snapshot-root').screenshot({ path: `${SNAP_DIR}/${slug}.png` })
}

/**
 * Fail if the mounted component is wider than the frame it was mounted in — the
 * "whole page scrolls sideways on a phone" bug.
 *
 * **A PNG cannot catch this on its own**: an element screenshot clips to the frame, so
 * the runaway content is simply cropped out and the image looks fine. That's why this
 * is an assertion rather than another snapshot.
 *
 * Mount with a `width` matching the viewport and call this *after* the assertions that
 * wait for content to land — a component measured mid-load is measured empty.
 *
 * Content inside a deliberate scroller (a pill row, a tab strip) is exempt: it
 * overflows its own box, not the page. That distinction is why this reads `scrollWidth`
 * on the frame rather than comparing every element's right edge.
 */
export async function expectNoHorizontalOverflow(page: Page) {
  const report = await page.evaluate(() => {
    const root = document.getElementById('snapshot-root')
    if (!root) throw new Error('#snapshot-root missing — call this after mount()')
    const rootRight = root.getBoundingClientRect().right
    const inOwnScroller = (el: Element) => {
      for (let p = el.parentElement; p && p !== root; p = p.parentElement) {
        const overflowX = getComputedStyle(p).overflowX
        if (overflowX === 'auto' || overflowX === 'scroll' || overflowX === 'hidden') return true
      }
      return false
    }
    // Named so a failure says which element ran wide, not just that something did.
    const widest = Array.from(root.querySelectorAll('*'))
      .map((el) => ({ el, rect: el.getBoundingClientRect() }))
      .filter(({ el, rect }) => rect.width > 0 && rect.right > rootRight + 1 && !inOwnScroller(el))
      .sort((a, b) => b.rect.right - a.rect.right)
      .slice(0, 5)
      .map(
        ({ el, rect }) =>
          `<${el.tagName.toLowerCase()}${el.className ? ` class="${el.className}"` : ''}> ` +
          `runs ${Math.round(rect.right - rootRight)}px past the right edge`,
      )
    return { scrollWidth: root.scrollWidth, clientWidth: root.clientWidth, widest }
  })
  expect(
    report.scrollWidth,
    [
      `Component overflows horizontally: ${report.scrollWidth}px of content in a ` +
        `${report.clientWidth}px frame.`,
      ...report.widest.map((line) => `  - ${line}`),
    ].join('\n'),
    // 1px of slack: scrollWidth/clientWidth are integer-rounded, so a subpixel layout
    // can read one over without anything actually overflowing.
  ).toBeLessThanOrEqual(report.clientWidth + 1)
}
