import { describe, expect, it } from 'vitest'
import {
  absoluteUrl,
  escapeHtml,
  injectPage,
  routeOutputFile,
  sitemapXml,
  type PrerenderRoute,
} from '../src/prerender'

const SHELL = `<!doctype html>
<html>
  <head>
    <title>site — default</title>
    <meta
      name="description"
      content="default description"
    />
    <link rel="stylesheet" href="/assets/index.css">
  </head>
  <body>
    <div id="root"></div>
  </body>
</html>`

const route: PrerenderRoute = {
  path: '/guides/example',
  title: 'Example <Guide> & "More" | site',
  description: 'An example guide.',
  jsonLd: { '@type': 'ItemList', name: 'closing </script> tag' },
}

describe('injectPage', () => {
  const page = injectPage(SHELL, route, '<main>hello</main>', 'https://example.com')

  it('swaps title and description, escaped', () => {
    expect(page).toContain('<title>Example &lt;Guide&gt; &amp; &quot;More&quot; | site</title>')
    expect(page).toContain('<meta name="description" content="An example guide." />')
    expect(page).not.toContain('site — default')
    expect(page).not.toContain('default description')
  })

  it('adds canonical, og tags, and json-ld with </ escaped', () => {
    expect(page).toContain('<link rel="canonical" href="https://example.com/guides/example" />')
    expect(page).toContain('<meta property="og:url" content="https://example.com/guides/example" />')
    expect(page).toContain('<script id="seo-jsonld" type="application/ld+json">')
    expect(page).toContain('closing <\\/script> tag')
  })

  it('injects the body into #root and keeps the stylesheet', () => {
    expect(page).toContain('<div id="root"><main>hello</main></div>')
    expect(page).toContain('/assets/index.css')
  })
})

describe('routeOutputFile', () => {
  it('emits flat .html files for clean URLs (no trailing-slash redirects)', () => {
    expect(routeOutputFile('/guides', 'landing-rewrite')).toBe('guides.html')
    expect(routeOutputFile('/guides/foo', 'landing-rewrite')).toBe('guides/foo.html')
  })
  it('routes / to landing.html or skips it, never index.html', () => {
    expect(routeOutputFile('/', 'landing-rewrite')).toBe('landing.html')
    expect(routeOutputFile('/', 'skip')).toBeNull()
  })
})

describe('sitemapXml', () => {
  it('lists absolute urls with optional hints', () => {
    const xml = sitemapXml(
      [
        { path: '/', title: 't', description: 'd', changefreq: 'weekly', priority: 1 },
        { path: '/guides', title: 't', description: 'd' },
      ],
      'https://example.com',
    )
    expect(xml).toContain('<loc>https://example.com/</loc>')
    expect(xml).toContain('<loc>https://example.com/guides</loc>')
    expect(xml).toContain('<changefreq>weekly</changefreq>')
    expect(xml).toContain('<priority>1.0</priority>')
    expect(xml.indexOf('<changefreq>')).toBe(xml.lastIndexOf('<changefreq>'))
  })
})

describe('escapeHtml / absoluteUrl', () => {
  it('escapes attribute-relevant characters', () => {
    expect(escapeHtml('a & <b> "c"')).toBe('a &amp; &lt;b&gt; &quot;c&quot;')
  })
  it('handles the root path without doubling slashes', () => {
    expect(absoluteUrl('https://example.com', '/')).toBe('https://example.com/')
    expect(absoluteUrl('https://example.com', '/x')).toBe('https://example.com/x')
  })
})
