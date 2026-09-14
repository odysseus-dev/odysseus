# Changelog

All notable changes to Odysseus will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Fixed

- **auth: prevent spurious login redirect on mobile during text input**
  The global `window.fetch` 401 interceptor (`static/app.js`) unconditionally
  redirected to `/login` on any background API call that returned 401. On mobile,
  when the user backgrounded the app (switched apps, locked phone), session
  cookies could become stale. When they returned, background fetches (task
  notification polling every 30s, calendar refetch on tab resume, notes badge
  refresh) received 401 responses and triggered an immediate redirect to
  `/login`, which wiped the user's unsent text input and chat state.

  This was especially noticeable on mobile browsers which aggressively suspend
  background tabs and expire cookies faster than desktop browsers.

  The fix adds three improvements to the 401 interceptor:
  1. **Retry once after 2s** — catches transient 401s from stale cookies on
     mobile tab resume that resolve on retry. The retry is restricted to safe,
     idempotent requests (GET/HEAD/OPTIONS) so a state-changing request (POST,
     PATCH, DELETE) is never silently replayed; a failed one reaches its caller
     as-is and still triggers the login redirect.
  2. **Deduplication** — prevents multiple stacked redirects from concurrent
     background fetches (e.g. task polling + calendar refetch simultaneously
     hitting 401).
  3. **Typing guard** — if the user is actively typing in the message input
     when the redirect fires, it defers until the input loses focus, preserving
     the draft.
