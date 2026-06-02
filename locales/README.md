# Translations (`locales/`) — gettext PO

This is the **translator-facing source of record** for Odysseus translations, in
standard GNU gettext **PO** format. Working here gives translators the full
ecosystem — [Poedit](https://poedit.net/), [Weblate](https://weblate.org/),
[Crowdin](https://crowdin.com/), Lokalize, … — with Translation Memory,
glossaries, machine translation and spell-/quality-checks.

```
locales/
  messages.pot     # template: every English source string, empty translations
  ja.po            # Japanese
  pt-BR.po         # Portuguese (Brazil)
```

The browser still loads the compiled `static/locales/*.json` catalogs; the PO
files are **compiled to that JSON** by a script, so the runtime is unchanged.
English (`static/locales/en.json`) stays the canonical source of the `msgid`s.

```
en.json ──(template)──►  messages.pot ──►  ja.po / pt-BR.po
                                               │ (translate)
                                               ▼
                          po_to_json.py ──►  static/locales/ja.json … (runtime)
```

## Common tasks

| I want to… | Run |
|---|---|
| Translate / fix strings | Edit `locales/<code>.po` (Poedit/Weblate/your editor), then `python scripts/i18n/po_to_json.py` |
| Add a new language | `python scripts/i18n/make_language.py <code> --native "<Endonym>"`, translate `locales/<code>.po`, then `python scripts/i18n/po_to_json.py` |
| Pull in new/changed English strings | `python scripts/i18n/update_strings.py`, translate the new/`#, fuzzy` entries, then `python scripts/i18n/po_to_json.py` |

After any of these, `python scripts/i18n/check_locales.py` validates the runtime
catalogs.

## Why PO compiles to JSON (and isn't shipped directly)

No mainstream web stack ships raw `.po` to the browser — WordPress and Lingui,
for example, both compile `.po` → JSON at build time. We do the same:
`po_to_json.py` emits exactly the `{ "English": "translation" }` catalogs the
runtime already consumed, so adopting PO changed **no** runtime code. A test
(`tests/test_i18n_po.py`) asserts the compile is byte-for-byte identical to the
shipped JSON, so the two can never silently diverge.

## Scripts

All are pure-Python (stdlib only) and run the same on Windows and Linux. If the
system **gettext** tools (`msgmerge`, `msginit`, `msgfmt`) are installed they are
used where they add value (notably `msgmerge`'s **fuzzy matching**, which keeps a
translation when its English source is *reworded* instead of dropping it).

| Script | gettext analog | Purpose |
|---|---|---|
| `json_to_po.py` | — | Bootstrap/export: JSON catalogs → `locales/*.po` (one-time; needs a dotted-key English catalog to label context entries) |
| `po_to_json.py` | `msgfmt` | Compile `locales/*.po` → runtime `static/locales/*.json` + `index.json` |
| `make_language.py` | `msginit` | Seed a new `locales/<code>.po` from `messages.pot` |
| `update_strings.py` | `xgettext` + `msgmerge` | Refresh `messages.pot` from English and merge into every `.po` |

## Notes for translators

- **Context (`msgctxt`).** When one English string needs different translations
  in different places (e.g. “Email” the label vs. a channel name), it appears as
  separate `msgctxt`-tagged entries — translate each in context.
- **Placeholders.** `{name}`-style placeholders (flagged `python-brace-format`)
  must be kept verbatim in the translation; they are filled in at runtime.
- **Plurals / grammatical gender.** The current UI strings are flat (no run-time
  count/gender selection), so PO plural-forms aren't used yet. If a future string
  needs number- or gender-dependent forms, that's expressed with ICU
  MessageFormat in the value — independent of this PO workflow.
