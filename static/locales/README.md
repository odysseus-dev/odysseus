# Translations — how to add a language

Adding a language is a **drop-in**: one JSON file + one registry line. No code
changes, no rebuild. The UI, browser-language detection, and the backend all
pick it up from `index.json` automatically.

## Quick start (one command)

```bash
python scripts/check_locales.py scaffold fr "Français" "French"
```

This copies `en.json` → `fr.json` and registers it in `index.json`. Now open
`fr.json` and translate the values. Run the checker any time to see what's left:

```bash
python scripts/check_locales.py
```

## Or by hand

1. **Register it** — add an entry to `index.json` → `locales`:
   ```json
   { "code": "fr", "name": "French", "nativeName": "Français", "dir": "ltr" }
   ```
   Use `"dir": "rtl"` for right-to-left languages (Arabic, Hebrew, …).
2. **Translate** — copy `en.json` to `fr.json` and translate each value.

## Rules

- **`en.json` is the source of truth.** Its keys are the contract. Translate
  *values*, never rename *keys*.
- **Missing a key is fine** — it falls back to English. You can translate
  incrementally and ship partial coverage.
- **Keep `{placeholders}` intact** — `"Welcome back, {name}"` →
  `"Bon retour, {name}"`. The `{name}` token must survive verbatim.
- **Plurals** are objects of CLDR categories — fill in the ones your language
  uses (`one`, `other`, plus `zero/two/few/many` where relevant):
  ```json
  { "message_count": { "one": "{count} message", "other": "{count} messages" } }
  ```
- `_meta` is informational; update its `code`/`name` to match.

## Validate before committing

```bash
python scripts/check_locales.py
```

Reports per locale: missing keys, extra/orphan keys, empty values, and
placeholder mismatches. Exits non-zero on errors (missing keys / broken
placeholders), so it works in CI.
