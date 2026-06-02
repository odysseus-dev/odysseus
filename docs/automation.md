# Automation selectors

Odysseus is starting to mark its dynamically-rendered UI elements with
stable `data-*` attributes so RPA tools (UiPath, Power Automate,
Playwright, Selenium, etc.) can target them reliably.

The static layout in `static/index.html` already uses semantic `id`s
(e.g. `#memory-bulk-delete`, `#add-skill-btn`). Those work as-is.
This page covers the *dynamic* surfaces — lists and rows that JS builds
at runtime — where there was no stable hook before.

## Convention

| Attribute | Purpose |
|---|---|
| `data-testid` | What kind of element it is. Stable across releases. Use this as the primary selector. |
| `data-<entity>-id` | The instance ID when there's a meaningful one (e.g. `data-model-id`, `data-endpoint-id`). |
| Other `data-*` | State flags like `data-stale="true"`, `data-collapsed="true"`. |

Two design rules:

1. `data-testid` values are **kebab-case** and namespaced by the
   surface they live in (e.g. `model-picker-option`,
   `model-picker-provider-header`). This keeps selectors readable and
   avoids cross-surface collisions.
2. **Never put a translation-dependent string in `data-testid`.** Use
   IDs or slugs, not user-facing labels.

## Currently covered

### Model picker (`static/js/modelPicker.js`)

| Selector | Element |
|---|---|
| `[data-testid="model-picker-option"]` | A clickable model row |
| `[data-testid="model-picker-option"][data-model-id="<mid>"]` | A specific model |
| `[data-testid="model-picker-favorite"][data-model-id="<mid>"]` | Favorite toggle on a model row |
| `[data-testid="model-picker-section"][data-section="recent\|favorites\|all models"]` | Section header inside the picker |
| `[data-testid="model-picker-empty"]` | "No matching models" state |
| `[data-testid="model-picker-provider-header"][data-provider="<slug>"]` | Collapsible provider group header |
| `[data-testid="model-picker-provider-group"][data-provider="<slug>"]` | Container for a provider's models |

### Example: Playwright

```js
// Open the model picker (static element, has an id)
await page.click('#model-picker-btn');

// Pick a specific model
await page.click('[data-testid="model-picker-option"][data-model-id="llama3.2:3b"]');

// Or just star/unstar a model
await page.click('[data-testid="model-picker-favorite"][data-model-id="llama3.2:3b"]');
```

## Roadmap

This is a starting point. Other surfaces that would benefit most from
the same treatment, in rough priority order:

- Session list in the sidebar (each chat row)
- Chat message rendering (each rendered message)
- Cookbook model rows (scan / download / serve)
- Email inbox rows
- Notes / Tasks / Calendar rows

Contributions welcome — keep the convention consistent and prefer
small, surface-by-surface PRs over sweeping edits.
