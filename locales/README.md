# Localization

Odysseus uses gettext-style `.po` files as the source of truth for UI strings.
The browser consumes generated JSON catalogs under `static/locales/`.

To update translations:

```sh
npm run i18n:compile
```

Commit both the edited `.po` files and generated JSON. CI-friendly checks can
use:

```sh
python scripts/i18n/compile_po.py --check
```

Use English UI text as `msgid` values. Empty or fuzzy translations are omitted
so the runtime falls back to the source English string.
