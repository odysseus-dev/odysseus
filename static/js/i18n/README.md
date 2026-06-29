# Internationalization (i18n)

Odysseus uses a lightweight **gettext-style** layer: the **English source string is
the translation key**. `t('Save')` returns the active-language translation when one
exists, otherwise it returns the key (English) unchanged.

- One dictionary per language to maintain (currently `pt-BR`). English needs none —
  it is the automatic fallback.
- Default language is **pt-BR**; users switch to English in
  **Settings → Appearance → Language**. The choice persists in `localStorage`
  (`odysseus-language`) and `/api/prefs/language`, and is applied early by an inline
  script in `index.html` (`window.__ODY_LANG`).
- The runtime accepts only the exact codes `pt-BR` and `en`. `normalizeLang()`
  maps every other value to `pt-BR`, including values read from storage or the
  preferences endpoint.
- On startup, a different server preference is applied through `setLang()` so
  storage, the active runtime, and the server remain aligned. An already-active
  preference only synchronizes the selector and local storage.

## Core API — `static/js/i18n.js`

```js
import {
  t, registerMessages, getLang, setLang, translateDOM, normalizeLang,
} from '../i18n.js';

t('Save')                                  // -> 'Salvar' (pt-BR) | 'Save' (en)
t('Deleted {n} item(s)', { n: 3 })         // -> 'Excluído(s) 3 item(ns)'
```

`t` is also exposed as `window.t` so deeply-nested files can call it without an
import. `translateDOM(root)` sweeps the static DOM and translates any text node /
`placeholder|title|aria-label` whose trimmed value is a known key (no-op in English;
add `data-i18n-skip` on an element to opt out its entire subtree, including
translatable attributes on descendants).

## Adding translations for a module

1. Create `static/js/i18n/<module>.pt-BR.js`:

   ```js
   import { registerMessages } from '../i18n.js';
   registerMessages('pt-BR', {
     'Copied': 'Copiado',
     'Deleted {n} task(s)': 'Excluída(s) {n} tarefa(s)',
   });
   ```

2. Wrap user-facing literals in the module with `t('...')`
   (`` `Deleted ${n} tasks` `` → `t('Deleted {n} task(s)', { n })`).

3. Load the dictionary once from the module's entry file:
   `import './i18n/<module>.pt-BR.js';` (adjust `../` for nested files).

**Do not translate:** code identifiers, `console.*`/debug output, CSS, URLs, API
paths, model IDs, `data-*` values, regex, emoji shortcodes. Only what a user reads.
