# F1 runtime and architecture assessment

Assessment date: 2026-10-13. Repository: `odysseus-dev/odysseus`.
Worktree: `worktrees/architecture/frontend-foundation`.
Branch: `refactor/frontend-architecture-foundation`.
HEAD, local `upstream/dev`, and the live GitHub official `dev` commit were all
`8feb50e89468235cd3e0986da80edbd3acaf2c40`. The initial worktree was clean.
That commit is the merged PR #6495, selected Notes/Todos agent context.
`origin` points to the maintainer fork; integration targets official `upstream/dev`.

Inputs read completely: the 2026-10-13 frontend architecture RFC and coding brief
from Downloads, `specs/frontend.md`, repository instructions and existing tests.
The current user brief supersedes the coding brief's `origin/dev` wording and
defines the Notes state/workflow slice before reminders. The design is self-contained
in this directory and does not require access to Downloads.

Input provenance:

- RFC SHA-256: `11dedcbb9971f4d787f2721b9c27aa43299baba39ca91066d01507d2d69ac4f8`.
- Coding brief SHA-256: `e4882867617b9c732ce4f445e3bc2da0b4ec1315119f8226c1fd8c1acbacf29d`.

## Module graph and bootstrap

`node scripts/check_frontend.mjs --inventory` reproduces the parser-based inventory.
It excludes vendored `static/lib/` and committed editor build output. At this base
it scans 230 JS files, finds 707 static import/re-export edges and 108 literal
dynamic import edges, and finds no missing targets. No computed dynamic imports
were found in the original parser audit. The earlier existing-regex scan counted
814 edges; executable syntax parsing counts 815. Use parser evidence for design
and retain the existing regression guard unchanged.

| Area | Current boundary and implication |
| --- | --- |
| HTML shell | `static/index.html`: nonce-bearing boot scripts, CSS/preloads, direct ESM script tags, then app/init/a11y. Multiple entry roots mean module identity matters. |
| App composition | `static/app.js`: feature imports, globals, route/sidebar composition, session hydration and auth-fetch wiring. It eagerly imports Notes, Calendar, Chat, Documents and Settings. |
| Startup | `static/js/startupShell.js`, `sessions.js`, `init.js`: first-paint scheduling, deferred hydration, user-switch storage cleanup and cosmetic permission gates. Preserve boot ordering. |
| Lazy loading | `static/js/panels.js`: only `editor` is registered. A memoized import thunk shares successful/in-flight promises and retries failed imports. Do not label all panels lazy. |
| Optional libraries | `markdown.js` lazily loads vendored KaTeX/Mermaid. KaTeX and fonts are precached; Mermaid becomes cached after first use. Pyodide has a CDN dependency. |
| Feature packages | Settings registry/navigation/finder/lifecycle, Calendar utilities/reminders, Email wrapper/package and editor tools already demonstrate partial decomposition. |

Highest import fan-in: `ui.js` 77, `spinner.js` 55, `editor/state.js` 39,
`escMenuStack.js` 23, and storage/modal/z-order helpers 17 each. The measured
static strongly connected components are:

- Theme and UI.
- Email Inbox, compatibility wrapper, package index, AI reply, attachments,
  body rendering, menus, reader, settings page and unsubscribe.
- Cookbook, diagnosis, HW Fit, download, running and serve modules.

These are existing compatibility constraints, not approved patterns for new
source. Dynamic edges connect more features but do not themselves establish
initialization cycles. No global legacy dependency rewrite is attempted in F1.

## State, side effects and ownership

| Owner | Evidence and architectural treatment |
| --- | --- |
| Shared configuration | `appConfig.js` owns invalidatable settings/tool request promises and consumes a sessionStorage prefetch once. Retain single identity and writer invalidation. |
| Browser persistence | `storage.js` centralizes keys and safe access; feature files also use direct localStorage. `init.js` cleans storage on user changes. Preferences are not an authorization store. |
| Feature state | Notes has module variables, selection sets, undo/drafts and reminder state. Email exports a broadly mutable package state object; editor state is imported by 39 modules. Keep ownership explicit; do not expand these into a global store. |
| Import-time effects | Notes reads storage/window and schedules reminders. Email state reads localStorage. Theme/UI initialize from DOM readiness; modal manager installs global listeners. Chip/mobile helpers bind at startup. Moving an import can change behavior before any exported function is called. |
| Compatibility globals | App exposes controllers on `window`; Notes exposes `window.notesModule`. They are compatibility bridges, not the new inter-feature API convention. |
| Async ownership | Session hydration, config invalidation, draft serialized saves and request-generation guards already prevent stale completion. New slices must preserve these rather than only move declarations. |

Good patterns to retain: narrow zero-state leaves, explicit wrapper export lists,
public API tests, retryable memoized lazy imports, shared config invalidation,
request-generation checks and source/package-aware regression helpers.

Patterns to avoid: blanket barrels exposing internals, feature imports from
generic helpers, cycles through coordinators, direct writes to another feature's
state or DOM, anonymous global listener ownership, import-triggered network/timer
initialization in new APIs, and helpers grouped by convenience instead of purpose.

## Runtime, assets and distribution

| Contract | Verified source |
| --- | --- |
| Static URL and revalidation | `app.py`: `register_static_mime_types`, `_RevalidatingStatic`, `/static` mount. JS MIME is `text/javascript`; JS/CSS/HTML have `Cache-Control: no-cache`. |
| App paths | `src/constants.py` and `src/runtime_paths.py`: checkout root in native mode, PyInstaller `_MEIPASS` in frozen mode; static assets follow app root, persisted data follows data-directory policy. |
| CSP | `core/middleware.py`: same-origin scripts, nonce and jsDelivr allowance; `src/app_helpers.py` injects HTML nonces. No CSP relaxation is needed for the proof. |
| Integrity | No `integrity=` attributes were found in the shell. Proof output is byte-identical and emits no source maps; no existing SRI or vendored artifacts change. |
| Runtime-generated assets | `/api/generated-image/...` uses a confined data path and owner handling; report/render endpoints, Blob manifests/icons and uploads are runtime concerns outside static source publication. |
| Docker | `Dockerfile` copies app code and existing static output; `.dockerignore` excludes node_modules. Node/npm in the image support Browser MCP, not frontend startup. |
| Native installers | `setup.py`, `start-macos.sh`, `launch-windows.ps1`, systemd service: Python dependencies/startup consume the checkout. No frontend build is invoked. |
| Windows desktop | `Odysseus.spec` and `build-windows-portable.ps1` include the whole static directory. Maintain output here rather than require a new bundle path or compiler. |
| macOS | `build-macos-app.sh` creates a launcher around the Python checkout. Static serving follows the existing checkout path. Installer execution was not performed on Linux. |

## PWA limitations and test coverage

`static/sw.js` matches exact URLs, including queries. Install fetches each
precache entry separately and tolerates failed fetches. It does not discover
transitive dependencies. HTML root and JS/CSS handlers are network-first;
other static assets are cache-first with background refresh. API and non-GET
requests bypass caching. Cache updates need explicit version/manifest review.
The older frontend spec's root stale-while-revalidate description is stale.

Registration in `static/index.html` currently requests no wider scope, so the
worker controls `/static/`, not `/`. Root offline reload is therefore a known
pre-existing gap. PR #6227 proposes the scope/header correction. F1 tests actual
offline ESM loading under the current scope and leaves that separate fix alone.
Offline APIs, first-use Mermaid and optional CDN runtimes are not certified.

Existing coverage includes runtime graph resolution and version parity; editor
offline graph coverage and lazy-loader retries; Email API/export/evaluation
coverage; Notes autosave, XSS, selection/listener and agent-context regressions;
MIME, nonce and runtime paths. Tests use pytest, Node's built-in runner and existing
Playwright Chromium. `tests/TESTING_STANDARD.md` requires behavior-first, isolated
tests. F1 adds real publication failures, architecture violations and an offline
browser proof. It does not replace existing source-sensitive tests.

## Open PR overlap, checked live on 2026-10-13

The open-PR listing was fetched through all pages; selected PR file lists were
inspected. Refresh this inventory before any later slice.

| PR | Overlap and disposition |
| --- | --- |
| [#6470](https://github.com/odysseus-dev/odysseus/pull/6470) | Theme/sidebar redesign touches HTML, SW, shared UI and CSS. F1 changes none of those assets. Future manifest/UI slices need collision review. |
| [#6695](https://github.com/odysseus-dev/odysseus/pull/6695) | OXS touches bootstrap, HTML, platform, middleware and other backend boundaries. F1 does not establish an extension API or modify that platform. |
| [#6227](https://github.com/odysseus-dev/odysseus/pull/6227) | Root SW scope/header fix. Independent follow-up dependency for broader offline certification. |
| [#6394](https://github.com/odysseus-dev/odysseus/pull/6394) | Older CSS extraction touches legacy style/SW assets; requires separate supersession review. |
| [#6627](https://github.com/odysseus-dev/odysseus/pull/6627) | npm development-dependency update overlaps package/lock files; reconcile the exact Acorn development entry on integration. |
| [#6585](https://github.com/odysseus-dev/odysseus/pull/6585), [#6647](https://github.com/odysseus-dev/odysseus/pull/6647), [#6652](https://github.com/odysseus-dev/odysseus/pull/6652) | Notes optional-field persistence and agent routing/dispatch. Future characterization must reflect their merged state; F1 changes no Notes or backend behavior. |
| [#6594](https://github.com/odysseus-dev/odysseus/pull/6594), [#6152](https://github.com/odysseus-dev/odysseus/pull/6152) | Desktop launcher/build and frozen-runtime packaging work. Existing paths stay stable; packaging still needs platform-owner validation. |

PR titles alone do not establish compatibility. This report records the checked
file overlap and relevant dependencies, not approval of those implementations.
