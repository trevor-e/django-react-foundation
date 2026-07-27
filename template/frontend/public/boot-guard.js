// Two things that must run *before* the app bundle, and therefore cannot live in it.
// This file is unhashed and served at the same path in every deploy, so it loads even
// when the hashed assets don't. Referenced from index.html with a plain <script>.

// 1. Refresh-during-a-deploy guard.
//
// If a page load lands on an index.html whose hashed /assets/ files aren't servable
// yet (mid-deploy edge propagation) or are already gone (stale cached HTML), the host
// serves index.html as the SPA fallback for the missing asset, the MIME check blocks
// the entry module, and the app never boots. Recovery can't live in the bundle —
// Vite's `vite:preloadError` handler only covers lazy chunks loaded *after* boot, and
// on this path the entry itself never ran, so that handler was never installed.
//
// On a failed /assets/ script or stylesheet, reload with a short backoff, at most 3
// times per rolling minute (sessionStorage — not a cookie) so a genuinely broken
// deploy can't loop forever.
;(function () {
  var KEY = 'assetBootFailures'
  var scheduled = false
  window.addEventListener(
    'error',
    function (event) {
      var el = event.target
      if (scheduled || !el || el === window || !el.tagName) return
      var url = el.tagName === 'SCRIPT' ? el.src : el.tagName === 'LINK' ? el.href : ''
      if (!url || new URL(url, location.href).pathname.indexOf('/assets/') !== 0) return
      var now = Date.now()
      var attempts = []
      try {
        attempts = JSON.parse(sessionStorage.getItem(KEY) || '[]').filter(function (t) {
          return now - t < 60000
        })
      } catch {
        attempts = []
      }
      if (attempts.length >= 3) return
      attempts.push(now)
      try {
        sessionStorage.setItem(KEY, JSON.stringify(attempts))
      } catch {
        // sessionStorage unavailable: still reload, just without the loop cap.
      }
      scheduled = true
      setTimeout(
        function () {
          location.reload()
        },
        1000 * attempts.length,
      )
    },
    // Capture phase: resource load errors don't bubble.
    true,
  )
})()

// 2. Appearance, before first paint.
//
// A strict CSP forbids inline scripts, so the theme class-setter lives here rather
// than in a <script> tag in index.html: unhashed, first-party, and loaded ahead of the
// bundle in every deploy — including prerendered pages. Without this, a dark-mode user
// sees a flash of light theme on every cold load.
//
// Preference is localStorage 'theme_preference': 'dark' | 'light' | absent (= follow
// the OS). localStorage, never a cookie. The package's `theme` module owns runtime
// changes; keep the key and the absent-means-system semantics in sync with it.
;(function () {
  var pref = null
  try {
    pref = localStorage.getItem('theme_preference')
  } catch {
    // Storage unavailable: fall through to the OS preference.
  }
  if (
    pref === 'dark' ||
    (pref !== 'light' && window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches)
  ) {
    document.documentElement.classList.add('dark')
  }
})()
