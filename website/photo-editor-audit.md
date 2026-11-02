# Photo Editor Product Audit

Date: 2026-08-30

## Executive Verdict

Odysseus already has the structure of a real layered raster editor. It is not a
mockup: layers, nested groups, masks, selections, retained text, blending,
history, document geometry, project recovery, controlled export, and several AI
workflows operate on an editable document model.

It is **roughly 78% of a dependable everyday photo editor**, but only **about
35% of a professional Photoshop/Photopea alternative**. Those are deliberately
separate scores. The first target needs complete, trustworthy common workflows;
the second also needs non-destructive sources and filters, color management,
professional file interchange, vector/path tooling, automation, and scale.

The main product gap is no longer basic layer infrastructure. It is the absence
of a strong photo-correction and retouching workflow, combined with insufficient
proof that every existing operation preserves pixels, masks, text, and project
state across desktop and touch.

## Current Scorecard

| Area | Everyday readiness | Current assessment |
| --- | ---: | --- |
| Canvas navigation and precision | 90% | Pan/zoom, fit and 1:1, rulers, guides, grid, snapping, and numeric geometry are present |
| Layers and compositing | 96% | Raster/text layers, nested groups, clipping, masks, blend modes, multi-select, locks, subtree reorder, and shared transforms are strong |
| Selections and masks | 97% | Marquee, lasso, wand, SAM, Quick Mask, named selections, affine selection transform, and linked/unlinked layer-mask positioning work |
| Text | 65% | Retained text exists; paragraph layout, tracking, font status, stronger hit testing, and mobile proof do not |
| Painting and retouching | 70% | Brush, eraser, clone, source-free and sampled healing, smudge, retained linear/radial gradients, fill, AI inpaint, background removal, sharpen, eyedropper, dodge/burn, and brush presets exist; advanced raster retouching remains |
| Photo correction | 65% | Retained levels, curves, exposure, white balance, hue/saturation, vibrance, shadows/highlights, color balance, selective color, gradient map, brightness/contrast, and histogram controls exist; camera/lens correction remains |
| Non-destructive editing | 60% | Text and adjustment metadata are retained, image imports and pasted selections create source-backed placed layers, raster layers can be converted to sources, and transforms/replacement preserve source pixels; linked instances and richer source editing remain |
| Save, recovery, and export | 88% | Versioned project recovery and PNG/JPEG/WebP export are strong; pixel-equivalence, metadata, and color-profile policies remain |
| Mobile editing | 45% | Responsive UI, touch navigation, and non-overlapping tool/layer sheets are tested; core editing gestures still need broader coverage |
| Performance and color fidelity | 30% | Safety limits exist; workers/tiles, stress evidence, ICC handling, and high-bit-depth editing do not |

## Verified Baseline

The current implementation was checked on 2026-08-30:

- **72 editor-focused unit tests pass.** Coverage includes the v12 document
  model, migrations, validation, geometry, selections, masks, mask offsets,
  groups, clipping, multi-select, transforms, retained text, history limits,
  guides, and export settings.
- **81 Chromium Playwright workflows pass.** They cover the layered core path,
  group masks and reorder, clipping, independent locks, multi-selection,
  nested groups, shared transforms, linked/unlinked mask movement and reopen,
  exact draft reopen, export dimensions, project recovery, Quick Mask, precise
  marquee geometry, transformed selections, named selections, and mobile touch
  crop, selection, brush, and transform gestures, plus mobile mask persistence,
  movement, and PNG export.
- Mobile editor refresh now has a dedicated recovery gate that restores the
  active draft, editor tab, saved status, canvas, and layer stack.
- The adjustment popup has a 320px phone-width regression gate: slider rows stay
  inside the sheet and the Apply/Cancel actions remain reachable.
- The Gradient Map adjustment has a dedicated 320px gate: its color controls
  collapse to one responsive column and remain inside the adjustment sheet.
- Every retained adjustment popup now has a 320px matrix check for viewport
  bounds, horizontal overflow, and reachable Apply/Cancel actions.
- The adjustment export matrix compares decoded PNG pixels against the visible
  composite for every retained adjustment family, including Curves and
  Gradient Map.
- The adjustment compositing workflow also compares export pixels when a
  retained adjustment is clipped, masked, opacity-modified, undone/redone, and
  reopened from a saved draft.
- The retained-adjustment export workflow passes in both Chromium and Firefox;
  the Playwright harness now supports selecting `chromium`, `firefox`, or
  `webkit` through `PHOTO_EDITOR_E2E_BROWSER`.
- Retained-effect previews now fall back cleanly when a worker cannot be
  created, and stale worker errors no longer trigger an unnecessary full-size
  synchronous render.
- The mobile layer-sheet workflow also verifies touch mask editing, undo/redo,
  and mask persistence after browser reload.
- The merge-fidelity workflow compares the rendered composite before and after
  Merge All with retained effects and adjustment layers, ensuring those edits
  are baked into the resulting raster instead of being dropped.
- The grouped-effects workflow compares a retained group effect against the
  exported PNG pixel-for-pixel and verifies its parameters survive draft reopen.
- The export preview workflow verifies matte pixels are cleared when switching
  back to transparency.
- The export dialog workflow restores focus to the Save control after closing,
  including the menu-launched export path.
- The core workflow now compares SHA-256 digests before and after draft reopen,
  requires exact decoded pixels for native PNG export, and bounds premultiplied
  pixel error for resized PNG output.
- The linked-mask regression gate verifies that an unlinked mask remains at a
  fixed document position when its parent layer moves or transforms. Brush,
  Wand, and Fill now resolve independently moved masks from the same origin.

This is solid Chromium/mouse evidence with focused touch coverage for crop,
selection, brush, and transform gestures, plus a focused Firefox export check.
It does not establish full Safari/Firefox compatibility, complete touch
reliability, large-document responsiveness, metadata or ICC fidelity, or
pixel-equivalent exports for every format.

## What Already Works

| Capability | Implementation status |
| --- | --- |
| Layered document | Raster and retained-text layers, nested groups, opacity, visibility, 16 Canvas2D blend modes, clipping, duplicate, merge, and hierarchy-aware reorder |
| Layer control | Multi-select, shared-bounds transforms, full/pixel/transparency/position locks, group masks, paintable layer masks, and linked/unlinked mask position |
| Selection model | Rectangle/ellipse marquee, lasso, Magic Wand, SAM, new/add/subtract/intersect, animated boundary, exact geometry, move/scale/rotate/flip, nudge, invert, Quick Mask, reselect, and named selections |
| Paint and AI | Brush, eraser, clone stamp, selection fill, inpaint, background removal, SAM, harmonize, upscale, denoise, face enhancement, and style operations |
| Geometry | Crop, image resize, canvas resize, rotate, flip, move, transforms, rulers, guides, grid, and snapping |
| Recovery | Validated v12+ project format, explicit migrations, bounded autosave/history, partial corrupt-project recovery, and exact server-draft reopen |
| Delivery | Previewed PNG/JPEG/WebP export with quality, dimensions, aspect lock, transparency/matte, filename, and gallery-copy flow |

## Release Blockers

These are the gaps that prevent calling the editor dependable today.

### P0: Trust Existing Operations

1. **Pixel-equivalent export proof is still too narrow.**
   The core layered workflow now proves exact native PNG pixels and bounded
   resized-PNG error, and format metadata is covered for JPEG/WebP. Grouped
   blending, clipping, matte pixels, and color-profile behavior still need the
   same evidence.

2. **Touch editing is only partially release-tested.**
   Pan and pinch primitives exist, and paint, selection, crop, and transform
   now have focused browser coverage, but broader mobile overlap and
   inaccessible-control assertions are still needed around edge cases.

3. **Operation contracts are incomplete.**
   Every destructive operation needs an explicit test matrix for raster layers,
   retained text, linked/unlinked masks, group masks, selections, locks, clipped
   layers, and multi-selection. The independently positioned mask bug found in
   Brush/Wand/Fill demonstrates why shared coordinate helpers are required.

4. **Large-document behavior is bounded, not proven.**
   Full-canvas Canvas2D compositing and RGBA history snapshots have hard limits,
   but there is no stress suite, cancellation contract, worker/offscreen path,
   memory telemetry, or degraded-preview strategy.

5. **Color and metadata behavior is undefined.**
   Import relies on browser decoding and export writes a new bitmap. Users are
   not told whether ICC, EXIF/IPTC, orientation, DPI, or location metadata is
   honored, normalized, preserved, or stripped.

## Missing Everyday Features

These have higher value than adding more isolated AI tools.

### P1: Photo Correction

- Composite and per-channel histogram with clipping warnings
- Adjustment presets, reset, and non-destructive before/after compare are implemented
- Actual adjustment layers that affect content below, can be clipped/grouped,
  and have their own masks; the current per-raster-layer stack is not equivalent

### P1: Retouching and Paint Ergonomics

- Richer multi-stop and radial gradient controls for raster content
- Healing Brush distinct from source-free Spot Healing and Clone Stamp
- Blur and Smudge brushes
- Content-aware fill workspace built on the existing inpaint capability

### P1: Geometry, Text, and Layer Workflow

- Image Size supports staged pixel/percentage resizing, aspect locking, and interpolation choice
- Canvas Size supports staged pixel/percentage bounds with anchor control
- Align and distribute selected layers (implemented in the multi-selection bar)
- Optional canvas auto-select for visible layers (group-level hit testing remains)
- Paragraph text boxes, tracking, vertical alignment, font loading/fallback
  status, and more reliable text hit testing
- Mask density, non-destructive feather, invert, disable, link/unlink position
  behavior, mask-only inspection, and applying true layer masks are implemented;
  applying a group mask still requires an explicit flattening workflow

## Missing Professional Features

These define the gap to Photopea/Photoshop rather than blocking a credible v1.

### P2: Non-Destructive Core

- Embedded source layers / smart-object equivalent (toolbar/gallery/drop imports, pasted selections, and manual raster-to-source conversion now exist; richer source editing remains)
- Editable transform matrices that preserve original pixels through repeated
  scale and rotate operations are now covered for placed and converted raster layers
- Linked instances and replace-source workflow
- Editable filter stacks with visibility, opacity, reorder, masks, and cached
  previews
- Blur, sharpen, denoise, high pass, lens correction, and perspective correction
  as retained filters
- Non-destructive transform masks, including perspective/warp later

This is the most important architectural gap. Photopea's Smart Objects retain a
separate source so repeated transforms can be recalculated without cumulative
loss, and its Smart Filters remain editable. Krita similarly models transform
and filter masks as non-destructive layer children.

### P2: File and Color Fidelity

- Explicit sRGB conversion and ICC profile awareness
- 16-bit processing before considering 32-bit/HDR
- EXIF/IPTC preservation or intentional stripping controls
- Reliable HEIC/TIFF handling and a RAW handoff/development path
- PSD import/export feasibility and a published compatibility matrix
- DPI/PPI and print-size metadata

### P2: Vector and Layout Work

- Shape layers for rectangle, ellipse, line, and custom paths
- Pen tool, editable Bezier paths, vector masks, and path-based selections
- Layer styles such as stroke, shadow, glow, and overlays
- Channels panel and channel operations
- Artboards only if multi-output design work is a product goal

### P3: Production Workflow

- Actions/macros, batch processing, and batch export
- Templates, reusable presets, and layer comps
- Soft proofing, gamut warning, and print output
- Plugin/filter extension surface
- Version history beyond the local bounded undo stack

## UX Audit

1. **The toolbar prioritizes AI before correction fundamentals.** Healing,
   Eyedropper, Gradient, and Curves should be as discoverable as SAM and Inpaint.
2. **The distinction between masks is still cognitively expensive.** Selection,
   Quick Mask, AI masks, layer masks, and group masks need consistent names,
   thumbnails, active states, and properties rather than relying on sub-row
   position alone.
3. **Properties are fragmented.** Tool controls, layer adjustments, transform
   values, and mask settings should use one contextual Properties area. This
   reduces modal popups and makes the selected target obvious.
4. **The editor needs clearer destructive-action signaling.** Blur, rasterize,
   merge, and applied transforms should say when source pixels will be replaced,
   with a one-step duplicate/convert-to-source option where appropriate.
5. **Mobile needs a deliberate mode.** Shrinking desktop controls is not enough;
   canvas-first editing needs bottom-sheet properties, stable touch targets,
   stylus behavior, and predictable two-finger navigation while a tool is active.
   The tool and Layers sheets now claim the viewport exclusively, and mobile
   layer/mask rows have stable non-scrolling layouts. Paint, crop, transform,
   mask save/reopen, mask movement, and layer reorder gestures are covered;
   mobile export now has viewport and download coverage, while broader
   multi-tool touch workflows remain.

## Credible V1 Definition

A dependable everyday editor is reached when all of these workflows pass as a
single checked-in desktop and touch gate:

1. Import a common web image, correct exposure/color, retouch a blemish, crop,
   resize, add text, and export at a chosen size and quality.
2. Build a layered composition with nested groups, clipping, a linked mask and
   an independently positioned mask, then close and reopen without state loss.
3. Apply, cancel, undo, and redo each geometry/filter operation without changing
   unrelated pixels or retained metadata.
4. Compare the flattened visible composite, reopened composite, and exported
   bitmap within a documented pixel tolerance.
5. Complete the same core workflow with mouse and touch, with no inaccessible
   controls, accidental page gestures, or silent partial edits.
6. Reject oversized, corrupt, or unsupported documents with a useful warning
   while retaining every recoverable layer.

## Recommended Build Order

### Milestone 1: Reliability Gate

- Expand composite-versus-export pixel tests across groups, clipping,
  adjustments, transparency/mattes, JPEG, and WebP.
- Add the operation/target matrix for masks, text, groups, clipping, locks, and
  multi-select.
- Add Chromium touch/mobile workflows and basic Firefox/WebKit smoke coverage.
- Centralize document/layer/mask coordinate conversion.
- Define import/export color and metadata policy.

**Exit:** a mixed 20-edit document survives undo/redo, close/reopen, and export
with equivalent visible pixels and editable state.

### Milestone 2: Everyday Photo Workflow

- Extend raster retained gradients with richer stop editing
  alongside the existing Eyedropper, Gradient, Healing, and Smudge tools.
- Add Curves, Exposure, White Balance, Vibrance, and Shadows/Highlights.
- Finish mask properties, Image/Canvas Size dialogs, text layout, alignment, and
  brush presets.

**Exit:** crop, correction, blemish removal, annotation, transparent assets, and
social-image composition can be completed locally without another editor.

### Milestone 3: Non-Destructive Editing

- Introduce source layers and editable transform matrices.
- Promote adjustments into real adjustment layers.
- Add retained filter stacks and filter masks.

**Exit:** normal experimentation no longer requires manually duplicating layers
to protect the original pixels.

### Milestone 4: Interchange and Scale

- Add worker/offscreen rendering, cancellation, telemetry, and stress tests.
- Implement color-profile and metadata policy.
- Run a PSD/HEIC/TIFF/RAW feasibility spike and publish compatibility limits.

## Architecture Direction

The current `layer.canvas + optional metadata` model is reaching its limit.
Before adding adjustment layers, vectors, or source layers, move to a typed
document contract:

```text
Layer = RasterLayer | TextLayer | GroupLayer | AdjustmentLayer | SourceLayer

common: id, name, visible, opacity, blendMode, locks, masks, transform
raster: mutable pixel surface
text: content and typography
group: ordered child ids
adjustment: operation, parameters, clipping, mask
source: immutable embedded pixels, editable transform, filter stack
```

Rendering, serialization, history, geometry, duplicate, merge, and thumbnails
should dispatch through that contract. Coordinate conversion should likewise be
centralized around document, layer, mask, and viewport spaces instead of being
reimplemented inside tools.

History should evolve toward commands plus periodic checkpoints. Bounded full
RGBA snapshots prevent runaway memory today, but remain expensive for large
documents and awkward for retained non-raster layer types.

## Benchmark Basis

This audit uses mature editors as behavior references, not as a requirement to
clone every feature:

- [Photopea feature map](https://www.photopea.com/learn/)
- [Photopea masks and mask properties](https://www.photopea.com/learn/masks)
- [Photopea adjustment layers and Smart Filters](https://www.photopea.com/learn/adjustments-filters)
- [Photopea Smart Objects](https://www.photopea.com/learn/smart-objects)
- [Krita transform masks](https://docs.krita.org/en/reference_manual/layers_and_masks/transformation_masks.html)
- [Krita non-destructive filters](https://docs.krita.org/en/reference_manual/filters.html)
- [Photoshop color-adjustment workflow](https://helpx.adobe.com/photoshop/using/color-adjustments.html)
- [Photoshop Content-Aware Fill](https://helpx.adobe.com/photoshop/desktop/apply-painting-techniques/fill-objects-selections-layers/content-aware-fills.html)

## Immediate Next Slice

Extend the new fidelity/mobile gate across **grouped blending, adjustments,
and real touch paint/transform gestures** next. Selection copy, cut, paste, and
single-step undo now have checked coverage.
After that, implement **Gradient + Healing Brush** as one vertical
everyday-photo slice rather than adding disconnected controls.
