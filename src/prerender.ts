// Build-time SSG for Vite SPAs (blueprint §SEO): render public marketing routes
// to static HTML with per-page head tags, plus sitemap.xml. Node-only — import
// from 'react-vite-foundation/prerender' in a post-build script, never from app
// code. No browser involved: pair with a small `vite build --ssr` entry that
// exports a `renderRoute(path)` built on react-dom/server's renderToString.
//
// Serving contract (Cloudflare Pages / Netlify style static hosts):
// - `dist/index.html` is the SPA fallback and is NEVER overwritten — prerendering
//   into it would flash marketing content on cold app-route loads.
// - '/' renders to `landing.html` plus a `_redirects` rewrite (`/ /landing 200`),
//   written only on successful prerender so a skipped build never rewrites to a
//   missing asset.
// - Other routes render to flat `<route>.html` files: clean-URL resolution serves
//   the exact canonical URL with no trailing-slash 308 (a `<route>/index.html`
//   layout would redirect).
import crypto from 'node:crypto'
import fs from 'node:fs'
import path from 'node:path'
import { authGateHeadSnippet, authGateScript } from './authGate'

export interface PrerenderRoute {
  /** Site-relative path: '/', '/guides', '/guides/foo'. */
  path: string
  title: string
  description: string
  changefreq?: 'always' | 'hourly' | 'daily' | 'weekly' | 'monthly' | 'yearly' | 'never'
  priority?: number
  jsonLd?: object
  /** Absolute URL of this route's own social-card image. Scrapers prefer the
   * document's FIRST `og:image`, so when the shell template already carries a
   * site-wide default, injectPage replaces that tag's content (appending a
   * second tag would leave the default winning); with no shell tag it appends.
   * When the URL lives under `siteOrigin`, prerenderSite requires the file to
   * exist in `distDir` and throws otherwise — a route can never ship a preview
   * image that 404s. Unset = whatever the shell declares. */
  image?: string
  /** `og:image:alt` to pair with `image`; defaults to the route title. */
  imageAlt?: string
}

export interface PrerenderOptions {
  /** The Vite client build output (contains index.html). */
  distDir: string
  /** e.g. 'https://example.com' — no trailing slash. */
  siteOrigin: string
  routes: PrerenderRoute[]
  /** Render a route to body HTML (typically the SSR entry's renderRoute). */
  render: (routePath: string) => string
  /** '/' handling: 'landing-rewrite' (default) emits landing.html + _redirects;
   * 'skip' leaves '/' client-rendered (it still appears in the sitemap). */
  rootStrategy?: 'landing-rewrite' | 'skip'
  /** Hide the prerendered landing pre-paint for logged-in users (authGate.ts):
   * injects the gate script+style into landing.html's head and, when a
   * `_headers` file with a `script-src 'self'` directive exists in distDir,
   * allowlists exactly that script by CSP hash. `true` uses the tokenStorage
   * default key. Pair with `liftAuthGate()` (root export) in the app's
   * landing-or-app switch. Only meaningful with rootStrategy 'landing-rewrite'. */
  authGate?: boolean | { storageKey?: string }
  /** Emit sitemap.xml (default true). */
  sitemap?: boolean
  log?: (message: string) => void
}

export function escapeHtml(value: string): string {
  return value
    .replaceAll('&', '&amp;')
    .replaceAll('<', '&lt;')
    .replaceAll('>', '&gt;')
    .replaceAll('"', '&quot;')
}

export function absoluteUrl(siteOrigin: string, routePath: string): string {
  return routePath === '/' ? `${siteOrigin}/` : `${siteOrigin}${routePath}`
}

/** Inject a route's head tags and rendered body into the SPA shell HTML. */
export function injectPage(
  template: string,
  route: PrerenderRoute,
  bodyHtml: string,
  siteOrigin: string,
): string {
  const href = absoluteUrl(siteOrigin, route.path)
  const headExtras = [
    `<link rel="canonical" href="${href}" />`,
    `<meta property="og:title" content="${escapeHtml(route.title)}" />`,
    `<meta property="og:description" content="${escapeHtml(route.description)}" />`,
    `<meta property="og:url" content="${href}" />`,
    route.jsonLd
      ? `<script id="seo-jsonld" type="application/ld+json">${JSON.stringify(route.jsonLd).replaceAll('</', '<\\/')}</script>`
      : '',
  ]
    .filter(Boolean)
    .join('\n    ')

  let page = template
    .replace(/<title>[\s\S]*?<\/title>/, `<title>${escapeHtml(route.title)}</title>`)
    .replace(
      /<meta\s+name="description"[\s\S]*?\/>/,
      `<meta name="description" content="${escapeHtml(route.description)}" />`,
    )

  if (route.image !== undefined) {
    // Replace the shell's site-wide og:image/og:image:alt in place (see the
    // PrerenderRoute.image doc: first og:image wins), appending only when the
    // shell declares none. The property value is matched with its closing
    // quote, so og:image:width/height/type are untouched.
    page = replaceOrAppendMeta(page, 'og:image', escapeHtml(route.image))
    page = replaceOrAppendMeta(page, 'og:image:alt', escapeHtml(route.imageAlt ?? route.title))
  }

  return page
    .replace('</head>', `    ${headExtras}\n  </head>`)
    .replace('<div id="root"></div>', `<div id="root">${bodyHtml}</div>`)
}

function replaceOrAppendMeta(page: string, property: string, content: string): string {
  const tag = `<meta property="${property}" content="${content}" />`
  const existing = new RegExp(`<meta\\s+property="${property}"[\\s\\S]*?/>`)
  return existing.test(page)
    ? page.replace(existing, tag)
    : page.replace('</head>', `    ${tag}\n  </head>`)
}

export function sitemapXml(routes: PrerenderRoute[], siteOrigin: string): string {
  const urls = routes
    .map((route) => {
      const lines = [`    <loc>${absoluteUrl(siteOrigin, route.path)}</loc>`]
      if (route.changefreq) lines.push(`    <changefreq>${route.changefreq}</changefreq>`)
      if (route.priority !== undefined)
        lines.push(`    <priority>${route.priority.toFixed(1)}</priority>`)
      return `  <url>\n${lines.join('\n')}\n  </url>`
    })
    .join('\n')
  return `<?xml version="1.0" encoding="UTF-8"?>\n<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">\n${urls}\n</urlset>\n`
}

/** Output file for a route under the serving contract above; null = not written. */
export function routeOutputFile(
  routePath: string,
  rootStrategy: 'landing-rewrite' | 'skip',
): string | null {
  if (routePath === '/') return rootStrategy === 'landing-rewrite' ? 'landing.html' : null
  return `${routePath.slice(1)}.html`
}

/** CSP source expression for an inline script: 'sha256-<base64>' of its bytes. */
export function cspScriptHash(js: string): string {
  return `sha256-${crypto.createHash('sha256').update(js).digest('base64')}`
}

/** Add an inline-script hash to the `script-src 'self'` directive of a
 * static-host headers file. Throws when the directive is missing: a headers
 * file whose CSP we can't extend would silently block the gate script in
 * production, so the build must fail instead. */
export function patchScriptSrcHash(headersText: string, hash: string): string {
  const directive = "script-src 'self'"
  if (!headersText.includes(directive)) {
    throw new Error(
      `prerender authGate: _headers exists but has no \`${directive}\` directive to ` +
        'extend — add the hash to your CSP manually or align the directive',
    )
  }
  return headersText.replace(directive, `${directive} '${hash}'`)
}

export function prerenderSite(options: PrerenderOptions): string[] {
  const {
    distDir,
    siteOrigin,
    routes,
    render,
    rootStrategy = 'landing-rewrite',
    authGate = false,
    sitemap = true,
    log = console.log,
  } = options

  const template = fs.readFileSync(path.join(distDir, 'index.html'), 'utf8')
  const written: string[] = []
  const gateKey = typeof authGate === 'object' ? authGate.storageKey : undefined

  for (const route of routes) {
    const outFile = routeOutputFile(route.path, rootStrategy)
    if (outFile === null) continue
    if (route.image !== undefined && route.image.startsWith(`${siteOrigin}/`)) {
      // A same-origin card must be in this build — fail instead of shipping a
      // preview URL that will 404 (cross-origin images can't be checked here).
      const imageFile = path.join(distDir, new URL(route.image).pathname)
      if (!fs.existsSync(imageFile)) {
        throw new Error(
          `prerender: ${route.path} declares image ${route.image} but ` +
            `${path.relative(distDir, imageFile)} is missing from ${distDir}`,
        )
      }
    }
    let page = injectPage(template, route, render(route.path), siteOrigin)
    if (route.path === '/' && authGate) {
      page = page.replace('</head>', `${authGateHeadSnippet(gateKey)}</head>`)
    }
    const outPath = path.join(distDir, outFile)
    fs.mkdirSync(path.dirname(outPath), { recursive: true })
    fs.writeFileSync(outPath, page)
    written.push(outFile)
    log(`prerender: ${route.path} -> ${outFile}`)
  }

  if (rootStrategy === 'landing-rewrite' && routes.some((r) => r.path === '/')) {
    fs.writeFileSync(path.join(distDir, '_redirects'), '/ /landing 200\n')
    written.push('_redirects')

    if (authGate) {
      const hash = cspScriptHash(authGateScript(gateKey))
      const headersPath = path.join(distDir, '_headers')
      if (fs.existsSync(headersPath)) {
        const patched = patchScriptSrcHash(fs.readFileSync(headersPath, 'utf8'), hash)
        fs.writeFileSync(headersPath, patched)
        written.push('_headers')
        log(`prerender: auth gate on landing.html ('${hash}' added to _headers CSP)`)
      } else {
        log(`prerender: auth gate on landing.html (no _headers file; inline hash '${hash}')`)
      }
    }
  }
  if (sitemap) {
    fs.writeFileSync(path.join(distDir, 'sitemap.xml'), sitemapXml(routes, siteOrigin))
    written.push('sitemap.xml')
    log(`prerender: sitemap.xml (${routes.length} urls)`)
  }
  return written
}
