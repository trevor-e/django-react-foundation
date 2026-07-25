// SEO head-tag management for SPA + prerender setups (blueprint §SEO). Import
// from 'react-vite-foundation/seo' — this subpath (unlike the root) requires
// react, declared as an optional peer dependency.
//
// The model: index.html ships static site-wide tags, prerendered pages ship
// their own head tags, and at runtime the hook finds-or-creates the same tags in
// place and restores them on unmount. Mutating (rather than React 19 hoisted
// head elements) avoids duplicating the tags that are already in the document.
import { useEffect } from 'react'

export interface SeoTags {
  title: string
  description: string
  /** Absolute page URL — emits `<link rel="canonical">` + `og:url` when set. */
  canonicalUrl?: string
  /** Serialized into `<script id="seo-jsonld" type="application/ld+json">`. */
  jsonLd?: object
}

/** Apply tags to `document.head`, returning a restore function that puts every
 * touched tag back (or removes tags this call created). Framework-free — the
 * `useSeo` hook is a thin effect wrapper around it. */
export function applySeoTags({ title, description, canonicalUrl, jsonLd }: SeoTags): () => void {
  const restores: Array<() => void> = []

  const ensure = <T extends HTMLElement>(selector: string, make: () => T): T => {
    let el = document.head.querySelector<T>(selector)
    if (!el) {
      const created = (el = make())
      document.head.appendChild(created)
      restores.push(() => created.remove())
    }
    return el
  }
  const setAttr = (el: HTMLElement, attr: string, value: string) => {
    const prev = el.getAttribute(attr)
    el.setAttribute(attr, value)
    restores.push(() => (prev === null ? el.removeAttribute(attr) : el.setAttribute(attr, prev)))
  }
  const meta = (key: 'name' | 'property', id: string, content: string) => {
    const el = ensure(`meta[${key}="${id}"]`, () => {
      const m = document.createElement('meta')
      m.setAttribute(key, id)
      return m
    })
    setAttr(el, 'content', content)
  }

  const prevTitle = document.title
  document.title = title
  restores.push(() => {
    document.title = prevTitle
  })

  meta('name', 'description', description)
  meta('property', 'og:title', title)
  meta('property', 'og:description', description)

  if (canonicalUrl !== undefined) {
    const link = ensure('link[rel="canonical"]', () => {
      const l = document.createElement('link')
      l.setAttribute('rel', 'canonical')
      return l
    })
    setAttr(link, 'href', canonicalUrl)
    meta('property', 'og:url', canonicalUrl)
  }

  if (jsonLd !== undefined) {
    const script = ensure<HTMLScriptElement>('script#seo-jsonld', () => {
      const s = document.createElement('script')
      s.id = 'seo-jsonld'
      s.type = 'application/ld+json'
      return s
    })
    const prevJson = script.textContent
    script.textContent = JSON.stringify(jsonLd)
    restores.push(() => {
      script.textContent = prevJson
    })
  }

  return () => {
    for (const restore of restores.reverse()) restore()
  }
}

/** Keep the document's SEO tags in sync with the mounted page. */
export function useSeo(tags: SeoTags) {
  const { title, description, canonicalUrl, jsonLd } = tags
  useEffect(
    () => applySeoTags({ title, description, canonicalUrl, jsonLd }),
    [title, description, canonicalUrl, jsonLd],
  )
}
