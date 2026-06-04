# Reviewer — Frontend (Web)

**Scope:** `static/**/*.js`, `static/**/*.css`, `static/**/*.html`,
`**/*.html`, `**/*.svelte`.

Odysseus ships a vanilla-JS frontend (`static/js`, `static/app.js`) with
server-rendered HTML and a service worker (`static/sw.js`). Apply
[`../review-checks-common.md`](../review-checks-common.md) first, then the
domain checks below.

## Check

- **XSS** — no `innerHTML` / `insertAdjacentHTML` / template injection of
  unescaped user or model output. Prefer `textContent`, or escape
  explicitly. Flag `eval`, `new Function`, and `document.write`.
- **Token / data leakage** — auth tokens, API keys, and session data
  must not be written to the DOM, `localStorage` in plaintext where
  avoidable, console logs, or query strings.
- **Fetch & endpoints** — requests go to same-origin / configured
  endpoints; no hardcoded third-party URLs that exfiltrate data; handle
  non-200 responses (don't assume success).
- **Service worker / caching** — `sw.js` changes must not cache
  authenticated or user-specific responses indefinitely; bump the cache
  version when cached assets change.
- **Accessibility (RECOMMENDED)** — interactive elements are reachable
  and labelled; images have `alt`; color is not the only signal.
- **Correctness** — no unhandled promise rejections; event listeners
  cleaned up where elements are removed; no obvious layout-breaking CSS.

## Common false-positives — do NOT flag

- `innerHTML` with a static, developer-authored constant (no user data).
- Inline styles or class patterns already used widely in the codebase.
- Missing framework conventions — this is intentionally vanilla JS.

## Extend

Add checks here as frontend conventions emerge (e.g. a component pattern,
a sanitizer helper). Cross-cutting rules go in
[`../review-checks-common.md`](../review-checks-common.md).
