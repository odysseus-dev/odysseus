# Localization (i18n)

Odysseus localizes the UI by **matching rendered English text** against
source-keyed catalogs — no per-element annotation, no build step. Adding a
language or a string is editing JSON.

> **Translators:** the recommended workflow is the gettext **PO** files in
> [`locales/`](../../locales/README.md) (Poedit/Weblate/Crowdin, with Translation
> Memory, glossaries and MT), which compile to the `*.json` catalogs described
> here. Editing these JSON files directly still works and is fine for a quick
> one-off change.

## Quick reference

| I want to… | Do this |
|---|---|
| Translate an existing string | Edit its value in each `static/locales/<code>.json` |
| Add a string from a new feature | Write the English in HTML/JS normally; add `"English": "translation"` to each locale file |
| Add a whole new language | Copy `en.json` → `<code>.json`, translate the values, add it to `index.json` |
| Find what's still untranslated | `python scripts/i18n/check_locales.py`, or open `?i18ndebug` → `i18n.report()` |
| Make one word translate differently by context | `data-i18n="key"` on that element + an `_overrides` entry |

No code changes are needed for any of these. Details below.

## How it works

- `static/js/i18n-core.js` — pure lookup/interpolation logic (unit-tested).
- `static/js/i18n.js` — walks the DOM, swaps known English text for the active
  locale, and watches for JS-rendered UI via a `MutationObserver`. Chat
  messages, editors, and code (`.msg`, `[contenteditable]`, `pre/code`, …) are
  never translated.
- `static/js/langPicker.js` — fills the `#lang-picker` `<select>` (Settings →
  Appearance) from the registry.
- `static/locales/*.json` — the catalogs. `en.json` is the canonical key set.

A string is translated if its exact English text is a key in the active locale's
catalog. Unknown strings render in English (safe fallback).

## Add a language

1. Copy `en.json` to `<code>.json` (e.g. `es.json`).
2. Set `_meta` (`code`, `name`, `nativeName`, `dir` — `rtl` for ar/he).
3. Translate the values (keys stay the English source text).
4. Register it in `index.json` under `locales`.
5. `python scripts/i18n/check_locales.py` to verify.

The picker and `<html lang/dir>` update automatically. No code changes.

**Worked example — add Spanish:**

```bash
cd static/locales
cp en.json es.json          # es.json now has every English string as key=value
```

In `es.json`, set the header and translate values (untranslated values can stay
English for now — they fall back gracefully):

```json
{
  "_meta": { "code": "es", "name": "Spanish", "nativeName": "Español", "dir": "ltr" },
  "Appearance": "Apariencia",
  "Settings": "Configuración"
}
```

Add it to `index.json`:

```json
{ "code": "es", "name": "Spanish", "nativeName": "Español", "dir": "ltr" }
```

Verify and you're done — "Español" now appears in the Settings → Appearance picker:

```bash
python scripts/i18n/check_locales.py     # lists any strings still untranslated in es
```

## Translate / fix a string

Edit the value in each locale file:

```json
"Save to Documents": "ドキュメントに保存"
```

Keep `_meta`/`_overrides` first; source keys sorted (only matters for clean
diffs). Re-fixing a translation everywhere = change it once here.

## Add a new UI string (new feature)

Just write the English in HTML/JS as normal — it renders in English in every
locale immediately. To translate it, add `"<English>": "<translation>"` to each
locale file. To find what still needs translating:

- **Static:** `python scripts/i18n/check_locales.py` lists strings in `en.json`
  missing from each locale.
- **Runtime (best for JS-rendered text):** open the app with `?i18ndebug`,
  switch language, click around, then in the console run
  `copy(i18n.report().join('\n'))` — that's every untranslated string actually
  seen, ready to paste in.
- **Automated (CI):** `node scripts/i18n/coverage.mjs` logs in, switches locale,
  tours the main surfaces, and dumps the same `report()` list (see below).

Add new English strings to `en.json` too (it's the canonical set the checker
compares against).

## Matching rules (so keys match the rendered text)

- **Whitespace-insensitive:** lookup normalizes internal whitespace + trims, so
  `"More   Tools\n"` matches the key `"More Tools"`. You don't need to match
  spacing exactly. Outer whitespace around the string is preserved in output.
- **Case-sensitive:** `"More tools"` and `"More Tools"` are different keys. If the
  app uses inconsistent casing (e.g. a button label vs. its tooltip), add both
  variants, or fix the source to be consistent. `coverage.mjs` / `report()`
  surface these.

This layer localizes what the client renders. Server-emitted strings (error
details, emails) aren't covered yet; a small server-side helper reading the same
catalogs is a natural follow-up.

## Automated coverage (Playwright)

`scripts/i18n/coverage.mjs` is the automated version of the `?i18ndebug` tour —
use it to catch untranslated strings (and casing/whitespace variants) without
clicking by hand, and as a CI gate.

```
npm i -D playwright && npx playwright install chromium   # one-time
ODY_USER=admin ODY_PASS=<pw> node scripts/i18n/coverage.mjs
# env: BASE_URL (default http://127.0.0.1:7077), LOCALE (default ja),
#      OUT=missing.txt (write list), STRICT=1 (exit 1 if any missing)
```

Extend the tour by adding clicks for views your feature introduces — the more
surfaces it visits, the more complete the report.

## Context collisions (the "Open" verb vs. adjective case)

Source-keying maps one English string to one translation everywhere. When a
single English word genuinely needs different translations by context, opt that
element in:

```html
<span data-i18n="status.saved">Saved</span>
```

and add the override to the locale catalog:

```json
"_overrides": { "status.saved": "保存済み" }
```

`data-i18n` wins over source matching. Everything without it stays auto. The
build importer already emitted overrides for the known collisions (`Saved`,
`Email`, `Admin`, density/font labels, …) — apply the `data-i18n` attribute to
those specific elements when you want the non-default reading.

## Importing bulk translations (one-time)

`scripts/i18n/build_locale.py` converts legacy dotted-key catalogs into this
source-keyed format and auto-resolves collisions into `_overrides`:

```
python scripts/i18n/build_locale.py --en dotted/en.json \
    --target ja=dotted/ja.json --out static/locales
```

## Checks

```
python scripts/i18n/check_locales.py            # validate + completeness report
python scripts/i18n/check_locales.py --strict   # fail on any untranslated string
node --test tests/i18n_core.test.mjs            # JS core unit tests
python -m pytest tests/test_i18n.py             # catalog + validator tests
```
