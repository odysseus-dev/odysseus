# Photo Editor Remaining Scope

Date: 2026-08-29

## Current verdict

Odysseus is now a credible layered everyday editor, not an editor mockup. The
first nine roadmap phases are implemented: professional transform geometry,
shared direct-manipulation sessions, retained placed content, unified
selections and masks, a reusable brush/retouch engine, and retained text and
shape layers.

Phase 10 is functionally advanced but not closed. First-class adjustment layers
now support Levels, Curves, Exposure, White Balance, Brightness/Contrast,
Hue/Saturation/Lightness, Color Balance, Selective Color, and Gradient Map.
They participate in clipping, groups, masks, visibility, opacity, history, the
v14 document format, and flattening. Retained effects have since been added as
a separate ordered stack with Gaussian Blur, Color Overlay, Drop Shadow, and
Stroke, including editable colors, visibility, opacity, reorder, rasterize,
history, persistence, and migration.

Practical readiness estimate:

- Everyday layered photo editing: **about 88%**
- Dependable professional v1 described by the roadmap: **about 62%**
- Broad Photoshop/Photopea feature parity: **about 50%**

The remaining gap is dominated by large-document rendering outside the live
composite path, workspace consolidation, interchange/color policy, and release
proof rather than basic canvas tools.

## Verification snapshot

- The focused editor unit suite currently passes **31 tests** in Docker.
- The full photo-editor browser suite currently has **41 passing workflows**;
  the nested-group selection workflow initially exposed a row-hit regression,
  which now passes on isolated rerun after the slider-selection fix. The new
  group-effects workflow also passes.
- The new adjustment tests exercise deterministic pixel math, nested parameter
  normalization, retained metadata, undo/redo, clipping, masks, and draft
  reopen.
- The latest editor changes have not yet been rebuilt into the live `7011`
  container.

## Close Phase 10

This is the immediate release slice.

1. Finish the bounded preview path for large documents. Downsampled previews
   now keep control movement responsive and full resolution is restored for
   commit/export. Live worker composites now use generation checks, latest-only
   coalescing, and close/reopen invalidation; extend the same guarantees to
   remaining preview paths.
2. Add flattened-export versus reopened-project pixel comparisons for every
   adjustment family, including groups, clipping, masks, blend mode, and
   partial opacity.
3. Validate the color algorithms visually. White Balance and Selective Color
   are currently deterministic approximations, not color-managed photographic
   transforms.
4. Test every adjustment popup on phone and desktop viewports, including tall
   popups, color inputs, drag, Reset, Apply, Cancel, and Escape.
5. Decide the migration path for the older per-raster `adjLayers` stack. It can
   remain readable for compatibility, but new UI should converge on first-class
   adjustment layers instead of maintaining two competing concepts.
6. Bump static cache versions, rebuild the live container, and run a short
   visual smoke test on `7011`.

## Phase 11: Retained effects and filters

The retained-effects slice is implemented for raster/placed/text/shape-compatible
layer output: Gaussian Blur, Sharpen, Color Overlay, Drop Shadow, and Stroke
have editable colors/parameters, visibility, opacity, reorder, rasterize,
history, migration, and reopen support. Effect-specific masks, presets, and
group-level effects are also implemented and covered by focused browser tests.
Remaining work is:

1. Extend worker coverage to serialization and remaining preview paths.
   Thumbnail encoding, retained-effect rasterization, and live composite
   rendering now use a worker where OffscreenCanvas is available, with
   synchronous compatibility fallbacks. Generation invalidation, latest-only
   coalescing, and CPU loop cancellation protect live rendering.
2. Add explicit group-effect blend/ordering tests for nested groups and
   non-default blend modes, plus visual comparisons for effect stacks.

Introduce the renderer/worker cancellation boundary here rather than adding
more synchronous full-canvas filters that Phase 14 must immediately replace.

## Phase 12: Professional workspace

Consolidate fragmented popups into one contextual properties surface. Persist
panel layout by device class, add command search, expose stable document status,
and support multiple open documents with independent history, zoom, pan, and
selection. Mobile should use canvas-first sheets rather than compressed desktop
panels.

## Phase 13: Interchange and export

Harden orientation, transparency, metadata, and color behavior for PNG, JPEG,
and WebP first. Add copy/paste and drag/drop through placed layers. Treat
layered formats as explicit compatibility projects: unsupported PSD/TIFF/HEIC
features must be reported, never silently discarded. Odysseus project files
remain the lossless source of truth.

## Phase 14: Performance and recovery

Move remaining preview/pixel paths into workers. Thumbnail encoding,
autosave serialization, adjustment rendering, and retained-effect rendering
now have worker-backed paths with compatibility fallbacks. Add
dirty-region compositing, reusable render surfaces, cancellation tokens,
operation telemetry, a documented surface/history budget, autosave generations,
and a checked-in 4K multi-layer benchmark.

This phase is the main architectural risk. Canvas 2D remains a valid
compatibility renderer, but full-document synchronous passes will not scale to
professional documents.

## Phase 15: Assisted editing

Normalize generation, editing, inpainting, segmentation, restoration, and
upscaling behind capability-based endpoints. Keep model/provider names out of
editor logic. Requests must exclude chat memory, show progress, cancel safely,
and return named reversible layers with provenance. Manual tools remain fully
usable without an endpoint.

Much of the endpoint plumbing already exists; the remaining work is consistent
capability discovery, lifecycle safety, and editor-native result handling.

## Phase 16: Release gate

Run complete user journeys on Chromium and Firefox desktop plus representative
phone/tablet viewports. Add keyboard-only and accessibility coverage, mixed
20-edit persistence/export tests, failure recovery, and large-document stress
tests. No supported operation may silently flatten or discard retained state.

## Architecture debt to control

- `galleryEditor.js` is still a large orchestrator. Continue extracting domain
  modules as visible features move, without a broad rewrite.
- Legacy raster adjustment sublayers and first-class adjustment layers overlap.
  Converge on the first-class model.
- Pixel effects still rely heavily on synchronous full-canvas work.
- `static/style.css` carries substantial editor-specific surface area and needs
  clearer component boundaries before workspace customization expands.
- The repository worktree contains many unrelated changes. Editor release and
  merge decisions require a scoped diff or clean integration branch.

## Recommended execution order

1. Close and deploy Phase 10.
2. Build Phase 11 through a cancellable render boundary.
3. Consolidate the workspace in Phase 12.
4. Define color/metadata policy and complete Phase 13.
5. Finish worker rendering, stress, and recovery in Phase 14.
6. Normalize assisted editing in Phase 15.
7. Run the cross-browser professional release gate in Phase 16.

Do not expand into full PSD fidelity, CMYK production, RAW development, 3D, or
complete Photoshop parity before this critical path passes. Those are separate
product decisions, not prerequisites for a strong Odysseus editor.
