# Localization (i18n)

Odysseus has a single, shared translation system used by **both** the browser UI
and the Python backend. Catalogs are plain JSON, namespaced by dotted keys, with
**English (`en`) as the source of truth**. Any key a locale omits falls back to
English, then to the raw key — text never renders blank.

## Layout

```
static/locales/
  index.json     # registry: available locales (code, name, nativeName, dir)
  en.json        # source catalog — the canonical key set
  ja.json        # Japanese (falls back to en.json for missing keys)
static/js/i18n.js        # client runtime  (window.i18n + ES module)
static/js/langPicker.js  # Settings → Appearance language <select>
core/i18n.py             # server runtime  (same catalogs)
routes/i18n_routes.py    # GET /api/i18n/locales (registry + Accept-Language match)
```

## Adding a new language

One command scaffolds the file and registers it:

```bash
python scripts/check_locales.py scaffold fr "Français" "French"   # add --rtl for RTL
```

Then translate the values in `static/locales/fr.json`. Or do it by hand:

1. Add an entry to `static/locales/index.json` → `locales` (`code`, `name`,
   `nativeName`, `dir` — use `"rtl"` for Arabic/Hebrew/…).
2. Copy `en.json` to `<code>.json` and translate the values. You may delete keys
   you haven't translated yet — they fall back to English automatically.

That's it. The picker, browser detection, and backend negotiation pick it up
from the registry. No code changes. Validate with `python scripts/check_locales.py`.
See `static/locales/README.md` for the translator-facing guide.

## Localizing a string

### In static HTML (`index.html`, `login.html`, …)

Add a key to `en.json` (+ each translation), then mark the element:

```html
<span data-i18n="settings.nav.appearance">Appearance</span>          <!-- textContent -->
<input data-i18n-attr="placeholder:common.search, title:common.close"> <!-- attributes -->
<div  data-i18n-html="some.rich.key"></div>                            <!-- innerHTML (catalog-trusted) -->
```

The English text left in the markup is the design-time default: it's what an
extraction tool would pull and what shows if a catalog fails to load. Only put a
`data-i18n` on an element whose **entire** text is translatable — for a node that
mixes an icon/SVG with text, wrap just the text in a `<span data-i18n="…">`.

### In JavaScript

```js
import i18n from './i18n.js';            // or use the global window.i18n
el.textContent = i18n.t('common.save');
toast(i18n.t('chat.copied'));
```

Re-apply after injecting markup that contains `data-i18n`:

```js
container.innerHTML = template;
i18n.applyTranslations(container);
```

### In Python (server-originated user-facing text)

```python
from core.i18n import translate, negotiate

locale = negotiate(request.headers.get("accept-language"))
raise HTTPException(404, translate("errors.session_not_found", locale))
```

## Interpolation & plurals

Placeholders use `{name}`:

```json
{ "chat": { "greeting": "Welcome back, {name}" } }
```
```js
i18n.t('chat.greeting', { name: user });          // JS
translate('chat.greeting', locale, name=user)      # Python
```

Plurals: make the value an object of CLDR categories and pass `count`:

```json
{ "chat": { "message_count": { "one": "{count} message", "other": "{count} messages" } } }
```
```js
i18n.t('chat.message_count', { count: n });        // JS — Intl.PluralRules
translate('chat.message_count', locale, count=n)    # Python (en-family rule)
```

## How a locale is chosen (client)

`localStorage['odysseus-locale']` (explicit choice) → per-user server pref
(`/api/prefs/locale`, cross-device) → `navigator.languages` matched to an
available locale → registry `default`. Selecting a language in Settings writes
both `localStorage` and the server pref.

## Conventions

- **`en.json` is the contract.** Add keys there first; keep keys sorted within a
  namespace; never delete a key still referenced in markup/code.
- Namespacing: `area.subarea.thing` (e.g. `settings.nav.appearance`,
  `email.compose.send`). Group by UI surface, not by language.
- Keep keys stable — translators key off them. Rename = update every catalog.
- Don't concatenate translated fragments; use one key with `{placeholders}` so
  word order can change per language.

## Status

Foundation complete and wired as a working demo on the Settings navigation and
the Appearance → Language picker. Remaining UI strings are migrated into the
catalogs incrementally using the patterns above.
