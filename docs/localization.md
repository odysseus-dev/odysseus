# Localization

Localization is a core Odysseus feature, not an OML plugin.

The language runtime has to run on the login and first-run screens, set the
document language and direction before feature modules render, select the PWA
manifest, and participate in service-worker caching. A plugin loads too late to
own those surfaces reliably. OML can still add translated strings for a plugin's
own UI later, but locale selection and catalog loading belong to core.

## Supported locales

Odysseus follows Steam's **Full Platform Supported Languages** table. The
machine-readable contract and native language names live in
`static/i18n/registry.json`; its `source` field records the Steamworks page used
for the list. Arabic is included even though Valve documents its platform
support as different from the other entries.

Catalogs are loaded on demand. The service worker keeps the core shell precache,
adds the registry and English fallback, then caches a selected locale after its
first request. This avoids loading or precaching every catalog for every user.
English remains active until the user explicitly selects another language.

## Runtime API

`window.odysseusI18n` exposes:

- `ready` and `setLocale(locale)`
- `t(key, parameters)` for semantic catalog keys
- locale-aware number, date, relative-time, list, plural, and collation helpers

New and dynamically-created UI must use semantic keys. A bounded legacy bridge
captures the static application shell before feature modules render and uses
exact-string matching only. Once another module changes a captured node, that
node is permanently removed from legacy translation. Mutation observation is
limited to explicit `data-i18n` elements, so model output and user-authored
messages, notes, documents, email, and session titles are never guessed from
their text. Existing native and styled dialog fallbacks use exact catalog
matching plus bounded placeholder-template matching for captured UI messages;
unknown text is left unchanged.

The static shell, login, setup, signup, validation, authentication, and account
2FA surfaces are wired now. Feature modules that create controls after startup
must add semantic attributes or call `t()` as they are migrated. Catalog
coverage does not grant permission to translate arbitrary late DOM text: doing
that can silently alter session titles, document names, and other user data
that happens to equal an English UI phrase.

## Catalog maintenance

The checked-in source is authoritative. `en.json` and `ledger.json` are
generated snapshots: the ledger records each extracted source location and a
stable hash. Every locale catalog is checked in with the complete key set;
validation fails on missing or extra keys. Present entries must preserve
placeholders, entities, URLs, paths, commands, identifiers, brands, and
technical tokens. Runtime never generates, translates, or fills catalog text.

```bash
node scripts/i18n-catalog.mjs extract
node scripts/i18n-catalog.mjs check-sources
node scripts/i18n-catalog.mjs validate
node scripts/i18n-catalog.mjs manifests
```

Translations are maintained as reviewed locale JSON files, not generated at
runtime. Corrections belong directly in the appropriate catalog; validation
rejects missing or unknown keys, changed protected fragments, HTML injection,
machine marker leaks, unexpected scripts, and hidden Unicode format controls.
Executable strings remain byte-identical to English.

A normal catalog refresh is:

```bash
node scripts/i18n-catalog.mjs extract
node scripts/i18n-catalog.mjs validate
node scripts/i18n-catalog.mjs manifests
```
