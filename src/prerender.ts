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
import fs from 'node:fs'
import path from 'node:path'

export interface PrerenderRoute {
  /** Site-relative path: '/', '/guides', '/guides/foo'. */
  path: string
  title: string
  description: string
  changefreq?: 'always' | 'hourly' | 'daily' | 'weekly' | 'monthly' | 'yearly' | 'never'
  priority?: number
  jsonLd?: object
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

  return template
    .replace(/<title>[\s\S]*?<\/title>/, `<title>${escapeHtml(route.title)}</title>`)
    .replace(
      /<meta\s+name="description"[\s\S]*?\/>/,
      `<meta name="description" content="${escapeHtml(route.description)}" />`,
    )
    .replace('</head>', `    ${headExtras}\n  </head>`)
    .replace('<div id="root"></div>', `<div id="root">${bodyHtml}</div>`)
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

export function prerenderSite(options: PrerenderOptions): string[] {
  const {
    distDir,
    siteOrigin,
    routes,
    render,
    rootStrategy = 'landing-rewrite',
    sitemap = true,
    log = console.log,
  } = options

  const template = fs.readFileSync(path.join(distDir, 'index.html'), 'utf8')
  const written: string[] = []

  for (const route of routes) {
    const outFile = routeOutputFile(route.path, rootStrategy)
    if (outFile === null) continue
    const page = injectPage(template, route, render(route.path), siteOrigin)
    const outPath = path.join(distDir, outFile)
    fs.mkdirSync(path.dirname(outPath), { recursive: true })
    fs.writeFileSync(outPath, page)
    written.push(outFile)
    log(`prerender: ${route.path} -> ${outFile}`)
  }

  if (rootStrategy === 'landing-rewrite' && routes.some((r) => r.path === '/')) {
    fs.writeFileSync(path.join(distDir, '_redirects'), '/ /landing 200\n')
    written.push('_redirects')
  }
  if (sitemap) {
    fs.writeFileSync(path.join(distDir, 'sitemap.xml'), sitemapXml(routes, siteOrigin))
    written.push('sitemap.xml')
    log(`prerender: sitemap.xml (${routes.length} urls)`)
  }
  return written
}
