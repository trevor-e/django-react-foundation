import fs from 'node:fs'
import os from 'node:os'
import path from 'node:path'
import { describe, expect, it } from 'vitest'
import {
  absoluteUrl,
  escapeHtml,
  injectPage,
  prerenderSite,
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

  it('leaves the shell og:image alone when the route declares none', () => {
    const shell = SHELL.replace(
      '</head>',
      '<meta property="og:image" content="https://example.com/og/default.jpg" /></head>',
    )
    const out = injectPage(shell, route, '', 'https://example.com')
    expect(out).toContain('content="https://example.com/og/default.jpg"')
  })
})

describe('injectPage with a per-route image', () => {
  const shell = SHELL.replace(
    '</head>',
    `<meta property="og:image" content="https://example.com/og/default.jpg" />
    <meta property="og:image:width" content="2400" />
    <meta property="og:image:alt" content="site default" />
  </head>`,
  )
  const withImage: PrerenderRoute = {
    ...route,
    image: 'https://example.com/og/example.jpg?v=1&x=2',
    imageAlt: 'An example card',
  }

  it('replaces the shell og:image and og:image:alt in place — never a second tag', () => {
    const out = injectPage(shell, withImage, '', 'https://example.com')
    expect(out).toContain('<meta property="og:image" content="https://example.com/og/example.jpg?v=1&amp;x=2" />')
    expect(out).toContain('<meta property="og:image:alt" content="An example card" />')
    expect(out).not.toContain('og/default.jpg')
    expect(out).not.toContain('site default')
    expect(out.match(/property="og:image"/g)).toHaveLength(1)
  })

  it('keeps sibling og:image:width untouched (property match includes the closing quote)', () => {
    const out = injectPage(shell, withImage, '', 'https://example.com')
    expect(out).toContain('<meta property="og:image:width" content="2400" />')
  })

  it('appends when the shell has no og:image, with alt defaulting to the escaped title', () => {
    const out = injectPage(SHELL, { ...withImage, imageAlt: undefined }, '', 'https://example.com')
    expect(out).toContain('<meta property="og:image" content="https://example.com/og/example.jpg?v=1&amp;x=2" />')
    expect(out).toContain(
      '<meta property="og:image:alt" content="Example &lt;Guide&gt; &amp; &quot;More&quot; | site" />',
    )
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

describe('prerenderSite image existence guard', () => {
  const dist = () => {
    const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'prerender-test-'))
    fs.writeFileSync(path.join(dir, 'index.html'), SHELL)
    return dir
  }
  const imageRoute: PrerenderRoute = {
    path: '/guides/foo',
    title: 't',
    description: 'd',
    image: 'https://example.com/og/foo.jpg',
  }
  const options = (distDir: string) => ({
    distDir,
    siteOrigin: 'https://example.com',
    routes: [imageRoute],
    render: () => '<main></main>',
    sitemap: false,
    log: () => {},
  })

  it('throws when a same-origin image is missing from dist, naming route and file', () => {
    const distDir = dist()
    expect(() => prerenderSite(options(distDir))).toThrowError(/\/guides\/foo.*og\/foo\.jpg/s)
    fs.rmSync(distDir, { recursive: true, force: true })
  })

  it('writes the page when the image exists, and skips the check for cross-origin images', () => {
    const distDir = dist()
    fs.mkdirSync(path.join(distDir, 'og'))
    fs.writeFileSync(path.join(distDir, 'og', 'foo.jpg'), 'jpg')
    const routes: PrerenderRoute[] = [
      imageRoute,
      { path: '/guides/cdn', title: 't', description: 'd', image: 'https://cdn.example.net/x.jpg' },
    ]
    const written = prerenderSite({ ...options(distDir), routes })
    expect(written).toContain('guides/foo.html')
    expect(written).toContain('guides/cdn.html')
    expect(fs.readFileSync(path.join(distDir, 'guides', 'foo.html'), 'utf8')).toContain(
      '<meta property="og:image" content="https://example.com/og/foo.jpg" />',
    )
    fs.rmSync(distDir, { recursive: true, force: true })
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
