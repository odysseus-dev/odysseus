# Computed-style snapshot harness

The app CSS is an ordered multi-file cascade. Hundreds of selectors are
declared more than once and `!important` appears throughout, so the rendered
result is a function of **source order**. Extracting a block into its own file,
reordering `<link>` tags, or moving an `@media` rule can silently change which
declaration wins, and nothing else in the suite would notice.

This harness makes that falsifiable. It captures `getComputedStyle` over a
fixed element inventory, hashes the result, and compares it to a committed
baseline. It moves no CSS itself.

## What it covers

| Dimension | Values |
|---|---|
| Pages | `static/index.html` (app shell, 76 elements), `static/login.html` (14), the bench (586 selectors) |
| Viewports | 1440x900, 820x1000, 768x1024 (touch), 390x844 (touch) |
| Themes | dark (default) and `:root.light` |
| Density | default, `:root.density-compact`, `:root.density-spacious` |
| Properties | 122 pinned properties per element, plus every custom property on `:root` and `body` |

That is 676 elements x 24 variants = 16,224 element snapshots per run, in
about 21 seconds.

The **app shell** page measures real elements in the markup the server sends,
including modals - each one revealed on its own and re-hidden straight after,
so the measurements stay independent.

The **bench** page measures one synthesised element per selector, built from
the selector itself. Its selector list is evidence-driven: every selector
declared **more than once** in the app cascade that can be expressed as a static
compound chain (551 of them), plus a curated set covering chat, documents,
email, notes, calendar, settings, cookbook and gallery. Redeclared selectors
are the ones a reorder can actually flip, so they are the ones worth benching.
A bench element pins the cascade for that class combination; it does not pin
the markup that the JS produces.

Selectors the bench grammar cannot express are the gap: selector lists
(`a, b`), pseudo-elements, pseudo-classes, `:not()` and `:has()`. They are
skipped rather than approximated.

## Files

| File | Role |
|---|---|
| `inventory.json` | The fixed inventory: properties, variants, pages, elements, bench selectors |
| `baseline.json` | The committed digest plus per-element and per-variant hashes |
| `capture.mjs` | Playwright capture; raw values on stdout |
| `bench.html` | Empty page that loads the stylesheet; the capture mounts bench nodes into it |
| `../test_css_computed_style_snapshot.py` | The regression test |
| `../../scripts/css_snapshot.py` | Hashing, comparison, and the CLI |

## Running it

```bash
./venv/bin/python -m pytest tests/test_css_computed_style_snapshot.py
./venv/bin/python scripts/css_snapshot.py --check          # same comparison, standalone
./venv/bin/python scripts/css_snapshot.py --write-baseline # re-record
```

The CLI serves the repository on an ephemeral port itself, so it does not need
pytest. Under pytest the session static server is reused through
`ODYSSEUS_TEST_STATIC_ORIGIN`.

`npm ci` is required: the capture drives Playwright's Chromium. Without it the
browser tests skip.

## When the test fails

The failure names the elements and the variants whose hashes moved. To see
which *property* moved, capture both sides and diff:

```bash
./venv/bin/python scripts/css_snapshot.py --dump after.json
git stash && ./venv/bin/python scripts/css_snapshot.py --dump before.json && git stash pop
diff <(python -m json.tool before.json) <(python -m json.tool after.json)
```

Re-record the baseline only when the change in rendered style is **intended**
and reviewed. On a mechanical CSS extraction it never should be: an extraction
that preserves order produces an identical digest, and one that does not has
changed the UI.

## Determinism

The digest is only worth having if an unchanged stylesheet always produces the
same bytes, so the capture:

- strips every `<script>` from the document, leaving exactly the markup the
  server sends - no app module can mutate classes underneath the measurement;
- injects the theme and density classes into `<html>` *before* first paint
  rather than toggling them afterwards, so no CSS transition is ever
  mid-interpolation while `getComputedStyle` runs;
- removes `autofocus` before parsing: focus states are outside this inventory,
  and the browser's asynchronous autofocus step otherwise races the capture;
- pauses CSS animations at time zero and finishes CSS transitions before each
  measurement, including newly revealed modals and newly mounted bench nodes.
  Animation and transition declarations are still captured; the harness does
  not inject `animation: none` or `transition: none`;
- pins Chromium's standard font preference to `Times New Roman` via CDP,
  without overriding any author declaration;
- canonicalizes only the `BlinkMacSystemFont` family token to `"system-ui"`,
  the spelling Chromium uses for that alias on macOS. Other family names and
  their order remain significant;
- measures the `custom-system-prompt` element's `max-height` in `lh`, as opted
  into by its inventory entry. Its authored `30lh` resolves to different pixel
  heights with different fallback fonts; the line count remains significant;
- aborts images, fonts and media, which cost time and change nothing in the
  pinned property set;
- hides scrollbars, so a platform's scrollbar width cannot change the width
  that percentages and `auto` resolve against;
- pins `prefers-reduced-motion`, `forced-colors`, `prefers-color-scheme` and
  the device pixel ratio.

## What it does not cover

- **Layout geometry.** `width`, `height`, `top`/`left`/`right`/`bottom`,
  `transform` and `grid-template-*` resolve to used values that depend on text
  layout, so they are excluded rather than risk a baseline that only holds on
  one machine. A reorder that changes a size through a property in the pinned
  set is still caught; one that changes it only through an excluded property is
  not.
- **Pseudo-class states.** `:hover`, `:focus` and `:active` are not driven.
- **JS-applied classes.** State the app adds at runtime (collapsed sidebar,
  open panels, active tabs) is not represented beyond what the served markup
  and the bench selectors already carry.
- **Browser upgrades and additional platforms.** The original macOS baseline
  and Linux captures were compared property by property through exact hash
  recovery; the proven platform differences are now controlled above. A new
  capture on macOS has not been performed. New browser serialization changes
  still need investigation rather than automatic baseline regeneration.
- **The stylesheet is only one of the inputs.** `static/login.html` styles
  itself from an inline `<style>` block; it is in the inventory so the
  hand-mirrored token values there are pinned too.

## Adding coverage

Add an entry to `inventory.json` - an `{key, selector}` object under a page's
`elements` (with `"unhide": true` if it ships hidden, `"custom": true` to
include custom properties), or a selector string under `bench` - then
re-record the baseline. `test_baseline_covers_every_inventory_entry` fails if
the two go out of sync.

`test_capture_is_independent_of_elapsed_time_and_font_metrics` perturbs capture
timing and font metrics, checks autofocus suppression, and proves that animation
keyframes, metadata and relative line counts still affect measurements. The
existing cascade-order self-test still detects a reordered declaration.

The PR #40 canonical baseline and its exact justification are documented in
[`pr40-validation.md`](pr40-validation.md). All 122 properties and 676 inventory
elements remain covered; only one element/property opts into line-relative
measurement.
