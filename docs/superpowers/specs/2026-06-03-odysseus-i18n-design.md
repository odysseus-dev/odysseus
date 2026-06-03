# Odysseus i18n Design

## Goal

Add a maintainable internationalization layer for Odysseus so the app can keep
English as the source/fallback language while supporting translated UI strings.
The first implementation PR should prioritize a broad Simplified Chinese
translation because it is immediately useful to the contributor, while keeping
the architecture ready for Traditional Chinese, Spanish, and French follow-up
PRs.

## Background

Odysseus currently has no localization framework. User-facing text is embedded
in `static/index.html`, `static/login.html`, and many files under
`static/js/`. Server-side CLI/setup strings are also English, but the most
visible contribution should start with the web UI because that is where users
spend time and where language switching can happen without changing deployment
behavior.

`CONTRIBUTING.md` asks for focused, easy-to-review pull requests and suggests
opening an issue before large features. A single PR containing a framework plus
four complete translations would be hard to review. The best upstream strategy
is to introduce the framework and one high-quality translation first, then add
more language packs in separate PRs.

## PR Strategy

1. Open an issue or draft proposal describing the i18n approach.
2. PR 1: Add the i18n framework, English fallback catalog, language selection,
   and broad Simplified Chinese coverage.
3. PR 2: Add Traditional Chinese after reviewing terminology rather than doing
   a blind automatic conversion.
4. PR 3 and later: Add Spanish and French catalogs as machine-assisted,
   review-welcome translations.

This keeps each PR useful on its own and avoids bundling unrelated translation
quality discussions into the architecture PR.

## Scope For PR 1

PR 1 should cover the main logged-in product surface:

- Login and first-run auth UI.
- Sidebar, chat header, chat composer, model picker, export menu, and mode
  toggles.
- Settings, including the new language setting.
- Cookbook main tabs, hardware scan labels, filters, serve/download state, and
  dependency/status messages.
- Memory and Skills modal.
- Notes, Tasks, Calendar, Gallery, Documents/Library, Compare, Deep Research,
  Email, and common toasts/errors where practical.

PR 1 should not translate:

- Model names, provider names, engine names, API names, file extensions, code,
  logs, shell output, user content, chat history, document content, or fetched
  web/email text.
- Backend setup scripts and terminal output, unless a UI flow already displays
  those strings.
- Generated AI output.

## Architecture

Create a small browser-side i18n runtime instead of adding a build step or heavy
dependency. Odysseus serves static assets directly, so the implementation should
work without bundling.

Files:

- `static/i18n/en.json`: source and fallback messages.
- `static/i18n/zh-CN.json`: Simplified Chinese messages for PR 1.
- `static/js/i18n.js`: runtime loader, translation lookup, interpolation,
  language preference persistence, and DOM application helpers.
- `static/login.html`: load i18n early and mark static login strings.
- `static/index.html`: load i18n before app modules, add language selector UI
  in Settings, and mark static strings.
- Existing `static/js/*.js`: import or use a global `window.i18n` helper for
  dynamic strings.

The runtime should expose:

```js
window.i18n = {
  ready,
  t,
  setLanguage,
  getLanguage,
  getSupportedLanguages,
  applyToDocument,
};
```

`t(key, params)` returns the active-language value, falls back to English, and
finally falls back to the key when missing. `params` supports simple
`{{name}}` interpolation. The runtime should dispatch an `i18n:changed` event so
open windows can re-render labels when the language changes.

## Data Flow

1. On page load, the runtime picks the language from localStorage, then browser
   language, then English.
2. It loads English and the selected language catalog with `fetch`.
3. Static DOM nodes with `data-i18n`, `data-i18n-title`,
   `data-i18n-placeholder`, `data-i18n-aria-label`, or related attributes are
   updated once catalogs are loaded.
4. Dynamic JS calls `window.i18n.t(key, params)` when rendering UI or showing a
   toast.
5. Settings saves the language through the existing preference path where
   feasible and mirrors it to localStorage so login can localize before auth
   APIs are available.

## Error Handling

- Missing locale file: log a console warning and keep English.
- Missing key: return the English message when present, otherwise return the
  key. Do not throw during rendering.
- Malformed JSON: log a warning and keep English.
- Language changed while a modal is open: emit `i18n:changed`; modules that
  have cached strings should re-render their visible view where practical.

## Testing

Automated tests should stay small and reviewable:

- Add a JS/runtime test that verifies language fallback, interpolation, and DOM
  attribute application.
- Add a test that every key in `zh-CN.json` exists in `en.json`.
- Add a test that reports missing Simplified Chinese keys but allows a short
  documented skip list if PR 1 intentionally leaves obscure strings in English.
- Run `node --check` on changed JS files.
- Run the existing focused Python tests only if server routes are touched. The
  initial PR should avoid backend route changes unless the preference API must
  be extended.

Manual checks:

- Login page renders in English by default and in Simplified Chinese after
  choosing Chinese.
- Logged-in app changes language without reload where possible.
- Chat still sends messages and model picker still opens.
- Settings language selector persists across reload and logout/login.
- Common modals still fit text at desktop and mobile widths.

## Review Risks

The main risk is PR size. To reduce that risk, PR 1 should avoid broad markup
reformatting and should not move unrelated code. Add i18n keys in stable,
namespaced groups such as `common.*`, `auth.*`, `sidebar.*`, `chat.*`,
`settings.*`, `cookbook.*`, `memory.*`, `calendar.*`, and `tasks.*`.

Another risk is translation quality. The Simplified Chinese catalog should use
clear product UI language, not literal machine translation. Additional language
catalogs should be submitted separately so reviewers can accept or refine them
independently.

## Open Decisions

All core decisions are fixed for the first implementation:

- Client-side runtime only.
- English fallback is mandatory.
- Simplified Chinese is the first translated catalog.
- Traditional Chinese, Spanish, and French are follow-up PRs.
- No external i18n dependency in PR 1.
