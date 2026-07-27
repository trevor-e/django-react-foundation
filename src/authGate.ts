// Pre-paint auth gate for the prerendered-'/' serving contract (prerender.ts).
//
// The problem it solves: with the landing-rewrite strategy, a logged-in user
// hitting '/' paints the static marketing HTML before the JS bundle runs, then
// the app's client-side auth check redirects — a sub-second landing-page flash.
// The gate is a blocking inline <script> in landing.html's <head> that checks
// localStorage for a signed-in marker and, when present, sets an attribute on
// <html> that a companion <style> uses to hide #root pre-paint. The app lifts
// the gate with `liftAuthGate()` once React has replaced the prerendered DOM.
//
// Which key to point it at follows the auth mode: the access-token key under JWT
// auth (the default), or the session hint (`createSessionHint`,
// DEFAULT_SESSION_HINT_KEY) under session-cookie auth, where an HttpOnly cookie
// leaves JavaScript nothing else to read. Either way only presence is tested.
//
// Security: the script only checks key *presence* — it never reads the value
// into the DOM or sends it anywhere, and the server still authenticates every
// API call. Worst case for a spoofed key is a blank frame, then a bounce to
// login. Sites with a CSP that bans inline script allowlist exactly this script
// by hash — prerender.ts computes it from the injected bytes, so changing the
// key changes the hash in the same build.
//
// Browser-safe module (no node imports): the root export re-exports
// `liftAuthGate`; the build-time injection lives in prerender.ts (Node-only).
import { DEFAULT_ACCESS_TOKEN_KEY } from './tokenStorage'

/** Attribute the gate script sets on `<html>` while the gate is closed. */
export const AUTH_GATE_ATTRIBUTE = 'data-skip-landing'

/** The exact inline JS injected into landing.html — also the bytes the CSP
 * hash covers, so any edit here changes the hash in the same build. */
export function authGateScript(storageKey: string = DEFAULT_ACCESS_TOKEN_KEY): string {
  return (
    `try{if(localStorage.getItem(${JSON.stringify(storageKey)}))` +
    `document.documentElement.setAttribute('${AUTH_GATE_ATTRIBUTE}','')}catch(e){}`
  )
}

/** The full head snippet: gate script + the style that hides #root. */
export function authGateHeadSnippet(storageKey?: string): string {
  return (
    `<script>${authGateScript(storageKey)}</script>` +
    `<style>html[${AUTH_GATE_ATTRIBUTE}] #root{display:none}</style>`
  )
}

/** Call once React owns the DOM on the '/' route (e.g. a mount effect in the
 * landing-or-app switch component) — by then there is nothing left to flash. */
export function liftAuthGate(): void {
  document.documentElement.removeAttribute(AUTH_GATE_ATTRIBUTE)
}
