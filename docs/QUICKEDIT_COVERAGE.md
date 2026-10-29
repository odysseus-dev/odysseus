# Quick Edit Coverage Audit

Audit of how much of the PhotoCraft editor is reachable through **Quick Edit**
(the natural-language editor agent). Built against build **0.20.44** (HARNESS
bridge fan-out fix `4dd11826`) and client commit `e78b5a96`.

Quick Edit executes in two stages:

1. **Plan** — `static/js/editor/editor-agent.js` posts `{prompt, state,
   screenshot}` to `/api/editor/agent`. HARNESS `routes/editor_agent_routes.py`
   plans with the shared style guide (`src/editor_style_guide.py`) plus an LLM,
   validates every step against its own `OP_CATALOG`, then executes.
2. **Execute** — only `ai_edit_image` / `generate_image` run server-side; every
   other op is delivered to the page over the editor bridge (SSE) and run by the
   client control surface (`window.geEditorControl.invoke`, catalog in
   `static/js/galleryEditor.js`). A local fallback planner lives in
   `static/js/editor/nl-edit.js`.

There are **three** catalogs that must agree:

| Catalog | File | Owner |
|---|---|---|
| Client control ops | `static/js/galleryEditor.js` | PHOTOSHOP |
| Server `OP_CATALOG` | `routes/editor_agent_routes.py` | HARNESS |
| Style guide / planner | `src/editor_style_guide.py` + `nl-edit.js` | HARNESS + PHOTOSHOP |

Legend: **C** = in client catalog · **S** = in server `OP_CATALOG` (incl.
`NEW_OP_NAMES`) · **P** = the planner/style guide emits it for some phrasing.
A capability is **usable from Quick Edit** only when the planner can emit it
(**P**) and the op exists on the side that runs it.

---

## 1. Menu bar (12 menus)

| Capability | Trigger phrasing | Op | C | S | P | Status |
|---|---|---|---|---|---|---|
| File → Import | "open this image" | `document.importImage` | ✔ | ✔ | partial | OK |
| File → Load project | "open my project" | `document.openPsd` | ✔ | ✘ | ✘ | **Server gap** |
| File → Save / Save copy / Export PSD | "save as psd" | `document.savePsd` | ✔ | ✔ | ✔ | OK |
| File → Export | "export png" | `export.png` | ✔ | ✔ | ✔ | OK |
| File → Save project | — | (project store) | n/a | n/a | ✘ | Out of scope (not an edit op) |
| Edit → Undo/Redo/History | "undo" | `history.undo` / `redo` | ✔ | ✔ | ✔ | OK |
| Edit → Free Transform | "free transform" | `transform.start` | ✔ | ✔ | ✔ | OK |
| Edit → Transform → Rotate 90/180 | "rotate this layer 90" | `layer.rotate` | ✔ | ✔ | ✔ | OK (whole image vs layer routed by the fast path) |
| Edit → Transform → Flip H/V | "flip this layer" | `layer.flip` | ✔ | ✔ | ✔ | OK (whole image vs layer routed by the fast path) |
| Image → Fill | "fill the whole image with white" | — | ✘ | ✘ | ✘ | **No op** |
| Image → Canvas Size | "make the canvas wider" | `document.resizeCanvas` | ✔ | ✔ | partial | OK |
| Image → Image Size | "resize the image to 1200px" | `document.resizeCanvas` | ✔ | ✔ | partial | OK |
| Image → Rotate 90/180 (whole image) | "rotate the whole image 90" | `document.rotate`/all-layers | ✘ | ✘ | ✘ | **No control op** |
| Image → Flip H/V (whole image) | "flip the whole canvas" | `document.flip`/all-layers | ✘ | ✘ | ✘ | **No control op** |
| Image → Mode RGB/Grayscale | "make it black and white" | `adjustments.apply` black-white | ✔ | ✔ | ✔ | OK (mode via adjustment) |
| Image → Adjustments → Invert | "invert the colours" | `adjustments.apply` invert | ✔ | ✔ | ✔ | OK (fast path colour-inverts the active layer; "invert the selection" stays Select ▸ Inverse) |
| Image → Mode CMYK/Lab, Bits 16/32 | — | — | ✘ | ✘ | ✘ | Not implemented (disabled in UI) |
| Layer → New | "add a layer" | `layers.addRaster` | ✔ | ✔ | partial | OK |
| Layer → Duplicate | "duplicate the layer" | `layers.duplicate` | ✔ | ✔ | partial | OK |
| Layer → Group | "group the layers" | `layers.group` | ✔ | ✔ | ✘ | **Server OK, planner gap** |
| Layer → Rasterize | "rasterize the text" | `layers.rasterize` | ✔ | ✔ | ✔ | OK |
| Layer → Delete | "delete the layer" | `layers.delete` | ✔ | ✔ | partial | OK |
| Layer → Merge Down | "merge down" | `layers.mergeDown` | ✔ | ✘ | ✘ | **Server gap** |
| Layer → Merge All / Flatten | "flatten the image" | `layers.flatten` | ✔ | ✘ | ✘ | **Server gap** |
| Layer → Select All | "select all layers" | (multi-select) | ✔ | ✔ | ✔ | OK (fast path selects every layer) |
| Layer → Combine (unite/subtract/…) | "combine the shapes" | `_combineSelectedShapes` | ✔ | ✔ | ✔ | OK (fast path selects the shape layers and combines) |
| Layer → Fill | "fill the layer with blue" | `selection.fill` / flood | partial | ✘ | ✘ | **Gap** |
| Layer → Layer Mask (reveal/hide/apply/invert/feather) | "add a layer mask" | `layers.addMaskFromSelection` | ✔ | ✔ | ✔ | OK |
| Layer → Opacity | "set the layer opacity to 50%" | `layers.setOpacity` | ✔ | ✔ | partial | OK (fast path sets the active layer's opacity) |
| Layer → Blend Mode | "set the blend mode to multiply" | `layers.setBlend` | ✔ | ✔ | partial | OK (fast path sets the active layer's blend mode) |
| Type → Add | "add a title" | `text.add` | ✔ | ✔ | ✔ | OK |
| Type → Character | "make the text bigger" | `text.edit` | ✔ | ✘ | ✘ | **Server gap + planner gap** |
| Type → Paragraph | "centre the text" | `text.layout` | ✔ | ✔ | partial | OK |
| Type → Warp | "warp the text" | `_rerenderTextLayer` (warp) | ✔ | ✔ | ✔ | OK |
| Select → All/Deselect/Invert | "invert the selection" | `selection.*` | ✔ | ✔ | ✔ | OK |
| Select → Reselect | "reselect" | `_reselectSelection` | ✔ | ✔ | ✔ | OK |
| Select → Transform | "transform the selection" | `transform.start` | ✔ | ✔ | partial | OK |
| Select → Refine | "refine the edge" | `selection.feather/expand` | ✔ | ✘ | ✘ | **Server gap** |
| Select → Color Range | "select the blue sky" | `selection.colorRange` | ✔ | ✔ | partial | OK |
| Select → Select and Mask | "select the subject" | `selection.subject` | ✔ | ✔ | ✔ | OK |
| Select → Save | "save the selection" | `_saveNamedSelection` | ✔ | ✔ | ✔ | OK |
| Select → Load | "load the selection as sky" | `_loadNamedSelection` | ✔ | ✔ | ✔ | OK |
| Filter → every filter (30+) | "pixelate the image" | `filters.apply` | ✔ | ✔ | ✔ | OK (fast path covers pixelate/emboss/twirl/pinch/spherize/ripple/solarize/find-edges/crystallize/noise/despeckle/unsharp + blur/sharpen/vignette) |
| Filter → Blur → Motion / Zoom | "motion blur" / "zoom blur" | `filters.apply` (blur-motion / blur-zoom) | ✔ | ✔ | ✔ | OK (fast path maps motion blur and zoom blur to their Filter-menu variants) |
| Filter → Layer effects (drop shadow, stroke, colour overlay, outer glow) | "add a drop shadow" | `layers.addStyle` | ✔ | ✔ | ✔ | OK (fast path adds drop shadow / stroke / outer glow / colour & gradient overlay; colour parsed from the prompt) |
| View → Rulers/Grid/Snap/Clear guides | "show the grid" | `#ge-view-menu` | ✔ | ✔ | ✔ | OK |
| Window → panels | — | n/a | n/a | n/a | ✘ | UI-only |
| 3D / Plugins | — | — | ✘ | ✘ | ✘ | Not implemented (disabled in UI) |

## 2. Tools

Toolbar tools are interactive, not bridge ops, so coverage is about whether the
**effect** of a tool is reachable by prompt.

| Tool | NL-reachable? | Notes |
|---|---|---|
| Move / Transform | `transform.*` | OK (transform.start/apply/cancel) |
| Hand / Zoom | — | View only |
| Crop | `document.crop` + format guard | OK |
| Text / Shape | `text.add`, `text.layout` | Shape has no add-shape op — **gap** |
| Brush / Eraser / Dodge / Burn / Smudge / Clone / Heal / Sharpen | — | Interactive painting; no NL op — **gap** (out of scope for planner) |
| Gradient | — | **No op** |
| **Paint Bucket** | `fill.flood` | **Server gap + planner gap** (op exists client-side only) |
| Eyedropper | — | UI only |
| Marquee / Lasso / Wand / Quick Select / Object Select / Pen / Path | `selection.rectangle/ellipse/polygon/subject/…` | OK for region/reference selects; lasso/pen paths have `selection.polygon` only |
| SAM / Inpaint / Rembg | `selection.subject`, `ai.removeObject`, `ai.removeBackground` | OK |
| **Pixel brush + grid** | — | UI state only; no op — **gap** (acceptable: it is a brush mode, not an edit op) |

## 3. Right-click context menu

| Item | Backing op | Reachable by prompt? |
|---|---|---|
| Brush size / preset / Pixel Brush | `state` UI | No (interactive) |
| Select All / Deselect / Inverse / Feather / Expand / Contract / Border / Smooth | `selection.*` | Yes |
| Transform Selection / Refine Edge | `transform.start`, `selection.feather` | Partial |
| Apply / Cancel Crop | `document.crop` | Yes |
| Commit / Cancel / Free Transform | `transform.apply/cancel/start` | Yes |
| Add Text Layer / Rasterize Text | `text.add`, `layers.rasterize` | Yes (rasterize OK) |
| Copy Color Hex / Swap Colors | `state.color` | OK (fast path swaps / resets the foreground & background swatches) |
| Fill Selection w/ Foreground | `selection.fill` | OK (fast path fills the selection/canvas with the foreground or background colour) |
| Cut / Copy / Paste | `selection.copy/paste` | **Gap** (no planner intent) |
| Rotate 90/180, Flip H/V | `layer.rotate/flip` | **Server+planner gap** (same as Edit menu) |
| Undo / Redo | `history.*` | Yes |

## 4. Adjustments

`adjustments.apply` / `adjustments.applyMasked` cover all adjustment types
(brightness-contrast, white-balance, shadows-highlights, vibrance, clarity,
vignette, black-white, …). The style guide emits several. **Status: OK**
(planner covers the common ones; the long tail is reachable only via explicit
wording).

## 5. Filters

`filters.apply` / `filters.applyMasked` run every filter the UI lists. The
planner only emits **sharpen**, **blur-gaussian**, and **vignette** via intents.
**Gap (planner):** no intent for noise, pixelate, stylize, distort, render,
unsharp, sharpen-edges, colour-overlay/drop-shadow/stroke.
**Gap (HARNESS):** `filters.apply`/`applyMasked` are in the server catalog, so
the fix is style-guide intents, not catalog.

## 6. Layer styles

`layers.addStyle {type, params}` exists in both catalogs. **Planner never emits
it**, so "add a drop shadow to the title", "add a stroke", "colour overlay" fail.
**Gap (planner).**

## 7. Selection ops

`selection.rectangle/ellipse/polygon/fill/delete/copy/paste/feather/expand/
contract/selectAll/deselect/invert/colorRange/subject/samText/face/sky/
background/hasMask/maskBounds/exportMask/regionLuminance`. **Status: mostly OK.**
Missing: reselect, save-selection.

## 8. Document ops

| Capability | Op | C | S | P |
|---|---|---|---|---|
| Crop | `document.crop` | ✔ | ✔ | ✔ |
| Resize canvas / image | `document.resizeCanvas` | ✔ | ✔ | partial |
| **Extend canvas** | `document.extend` | ✔ | ✘ | ✘ |
| Save PSD | `document.savePsd` | ✔ | ✔ | ✔ |
| Export layers | `document.exportLayers` | ✔ | ✘ | ✘ |
| Duplicate / Create / Switch / List | `document.*` | ✔ | ✘ | partial |

## 9. AI edit & generate modes

| Mode | Op | Notes |
|---|---|---|
| Generative fill | `ai.generativeFill` | OK |
| Remove object | `ai.removeObject` | OK |
| Harmonize | `ai.harmonize` | OK |
| Upscale | `ai.generativeUpscale` | OK |
| Generate image | `ai.generateImage` / server `generate_image` | OK |
| Expand | `ai.generativeExpand` | OK (content-bounds prefill, SW v760) |
| Remove background | `ai.removeBackground` | OK |
| Instruction edit / scene replace | server `ai_edit_image` | OK (HARNESS) |

---

## 10. Gap list

### Client-side (fix in this repo — PHOTOSHOP-owned) — **ALL FIXED**
1. ✅ **`fill.flood` not in the NL path** — added to `nl-edit.js OP_CATALOG` +
   `localPlan` fill intent ("fill … with <colour>"; falls back to `selection.fill`
   when a region is selected).
2. ✅ **`layer.flip` / `layer.rotate` not in the NL path** — added to
   `nl-edit.js OP_CATALOG` + "this layer" intent (whole-image phrasing routes to
   `document.flip`/`document.rotate`).
3. ✅ **`text.edit` not in the NL path** — added catalog entry + intent for font
   size/colour ("make the text bigger/smaller", "make it red").
4. ✅ **`document.extend` not in the NL path** — added catalog entry + intent for
   "extend the canvas to 16:9" (derives target size from the document).
5. ✅ **`layers.align`** — added planner intent ("align … to the centre/left/…").
6. ✅ **`layers.addStyle`** — added planner trigger ("drop shadow", "stroke",
   "colour overlay").
7. ✅ **Whole-image rotate/flip has no control op** — added `document.rotate` /
   `document.flip` control ops (`galleryEditor.js`) and catalog/intent entries.
8. ✅ **`selection.copy/paste` intents** — added "copy the selection"/"paste".

### Server-side (file for HARNESS in the passover)
1. `routes/editor_agent_routes.py OP_CATALOG` is missing ops the client runs:
   `layer.flip`, `layer.rotate`, `text.edit`, `fill.flood`, `layers.align`,
   `layers.rasterize`, `layers.mergeDown`, `layers.flatten`, `document.extend`,
   `document.exportLayers`, `document.*` (list/duplicate/create/switch/openPsd),
   `mask.*`, `selection.*` (polygon/feather/expand/contract/copy/paste/hasMask/
   face), `transform.*` are partly present.
2. `src/editor_style_guide.py` has no intents for: layer transform
   (rotate/flip active layer), whole-document rotate/flip, text editing, align,
   layer styles (drop shadow/stroke/overlay), flood fill, copy/paste, noise/
   pixelate/stylize/distort/render filters.

## 11. Coverage summary

Counting distinct user-facing capabilities in the toolbar + 12 menus + right-click
(~150 including the filter/effect long tail):

- **Have a client op:** ~85%
- **Client op also in server OP_CATALOG:** ~75%
- **Planner can emit for natural phrasing:** ~72% (was ~55%; client-side
  planner intents for fill, layer/whole-image transform, text edit, align,
  layer styles, copy/paste and extend are now implemented)
- **Usable from Quick Edit end-to-end today:** **45/45 prompts (100%)** measured
  live on build **0.20.45** with `tests/e2e/photo-editor/quickedit-coverage.js`
  (`/tmp/opencode/qe-coverage45/quickedit-coverage.json`). HARNESS build 0.20.45
  (`4a6932a2`) added the 12 missing structural ops to the server `OP_CATALOG` +
  style guide v5, so every audited prompt now plans to the exact op.

The gap was dominated by the **planner**, not by missing client functionality;
that planner gap is now closed end-to-end (30/45 on 0.20.44 -> 45/45 on 0.20.45).

## 12. Test prompts (~40)

See `docs/QUICKEDIT_PROMPTS.md` and the runner
`tests/e2e/photo-editor/quickedit-coverage.js` for per-prompt pass/fail.

### Live run (build 0.20.45) — 45/45 PASS

All 45 prompts plan to the exact expected op. HARNESS 0.20.45 routes these
structural ops server-side: `fill.flood`, `text.edit`, `layers.align`,
`layer.rotate`/`layer.flip`, `document.rotate`/`document.flip`,
`selection.copy`/`selection.paste`, `layers.flatten`, `layers.mergeDown` (plus
the previously passing set). Results:
`/tmp/opencode/qe-coverage45/quickedit-coverage.json`.

### Previous run (build 0.20.44) — 30/45 PASS

Kept for provenance. Failures were all the server planner emitting a generic
`adjustments.apply`/`transform.apply` or a wrong op; ids 1, 2, 3, 4, 8, 9, 10,
11, 12, 13, 14, 19, 20, 40, 41. All now fixed in 0.20.45.


## Provenance

Recon inventory captured from `static/js/editor/build/topbar.js`,
`static/js/editor/build/toolbar.js`, `static/js/editor/canvas-context-menu.js`,
`static/js/galleryEditor.js` (`installEditorControl`), `static/js/editor/nl-edit.js`,
`routes/editor_agent_routes.py`, and `src/editor_style_guide.py`.
