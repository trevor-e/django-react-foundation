---
feature: SEO metadata, static prerendering, and pre-paint auth gating
code: [src/seo.ts, src/prerender.ts, src/authGate.ts, tests/prerender.test.ts, tests/authGate.test.ts]
---
# frontend-rendering

## Rules
- [frontend-rendering.spa-fallback] Prerendering never overwrites `dist/index.html`; that file remains the SPA fallback for application routes. {pre-kanspec}
- [frontend-rendering.clean-urls] The root landing page is emitted behind a rewrite and other public routes become flat HTML files so canonical clean URLs do not acquire trailing-slash redirects. {pre-kanspec}
- [frontend-rendering.atomic-output] Rewrites, headers, sitemap, and route files are published only after the complete prerender succeeds. {pre-kanspec}
- [frontend-rendering.social-image] Route-specific social metadata replaces the existing site-wide tag in place, and same-origin social images must exist in the output directory. {pre-kanspec}
- [frontend-rendering.head-restore] Runtime SEO updates find or create canonical head elements and restore their prior state on unmount instead of accumulating duplicate tags. {pre-kanspec}
- [frontend-rendering.auth-gate] The landing auth gate checks only marker presence before paint, hides only prerendered root content, and stays synchronized with its exact CSP script hash. {pre-kanspec}
