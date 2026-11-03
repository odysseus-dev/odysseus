# Plan: Odysseus Professional Photo Editor

> Source PRD: Conversation goal, "a Photoshop/Photopea clone with Odysseus style"

## Product boundary

Odysseus should provide the editing loop people expect from a professional
layer-based photo editor without copying Photoshop's visual design or trying to
match every specialist feature. The target is a dependable browser editor for
real photo work: direct manipulation, non-destructive layers, precise masking,
retouching, typography, export, recovery, and optional AI assistance.

The existing quiet Odysseus interface remains the visual language. Dense tools
are acceptable, but controls should stay restrained, compact, predictable, and
usable on both desktop and touch devices.

## Existing foundation

The current editor already provides meaningful parts of this product:

- Raster and editable text layers
- Multi-layer selection, nested groups, clipping, visibility, opacity, and locks
- Layer, group, and selection masks
- Marquee, lasso, wand, SAM, Quick Mask, and saved selections
- Brush, eraser, clone, crop, transform, and text tools
- Blend modes, adjustment stacks, blur, and several image corrections
- Rulers, guides, grid, snapping, zooming, and panning
- Undo/redo history with a memory budget
- Versioned layered-project serialization, autosave drafts, recovery, and export
- Optional endpoint-backed inpaint and image-processing tools
- Desktop and mobile editor layouts with Playwright release-gate coverage

## Architectural decisions

Durable decisions that apply across every phase:

- **Editor ownership**: The editor remains an Odysseus feature. Do not embed a
  third-party editor or imitate another product's chrome.
- **Document format**: Continue the versioned Odysseus editor document. Every
  new persistent capability requires a migration, validation, round-trip test,
  and corrupt-input recovery behavior.
- **Layer model**: Grow the document into explicit layer kinds rather than
  hiding more behavior in raster canvases. The intended kinds are raster, text,
  shape, adjustment, and placed/smart content.
- **Non-destructive default**: Preserve source pixels and editable parameters
  whenever practical. Destructive actions remain available as explicit Apply,
  Rasterize, or Merge commands.
- **Interaction engine**: Transform, crop, selections, text frames, masks, and
  shapes share one pointer-session model for hit testing, pointer capture,
  modifiers, snapping, cancellation, and undo transactions.
- **Rendering**: Keep Canvas 2D as the compatibility renderer initially. Move
  expensive compositing and pixel operations behind renderer/worker boundaries
  before considering WebGL or WebGPU acceleration.
- **History**: One continuous gesture creates one undo entry. Preview frames are
  never separate history entries, and Cancel restores the exact starting state.
- **Persistence routes**: Continue using `/api/editor-drafts` for layered draft
  persistence and `/api/gallery` for media-library save/replace operations.
- **AI boundary**: AI features consume capability-based image endpoints. Core
  editing never requires a particular model, repository, or provider.
- **Responsive behavior**: Desktop favors precision; touch targets gain larger
  invisible hit areas without visually enlarging the whole interface.
- **Testing**: Every phase adds deterministic geometry/unit tests and at least
  one complete Playwright workflow covering persistence and undo where relevant.
- **Incremental architecture**: New behavior leaves the main editor orchestrator
  through small domain modules. Avoid broad refactors that do not deliver a
  visible editing improvement in the same phase.

---

## Phase 1: Accurate Transform Frame

**User stories**: I can clearly see and grab the transform frame at any zoom. I
can resize from corners or sides without grabbing invisible or incorrect areas.

### What to build

Replace the four-corner-only frame with a shared frame geometry model. Render
four corners, four edge handles, a rotation control, and an optional center
pivot from the same geometry used for hit testing. Keep handles visually compact
while providing touch-sized invisible targets. Make the frame stay aligned
during zoom, pan, viewport resize, and when handles extend outside the image.

### Acceptance criteria

- [x] Eight resize handles, rotation control, and center pivot derive from one geometry result.
- [x] Drawn handles and hit targets cannot disagree.
- [x] Handles remain a stable visual size from minimum to maximum zoom.
- [x] Touch hit targets are at least 40 CSS pixels without oversized visuals.
- [x] Outside-canvas handles remain interactive and visible when space permits.
- [x] Hover and active cursors match each handle's current screen direction.
- [x] Desktop and mobile Playwright tests grab every handle successfully.

---

## Phase 2: Correct Rotated Resize

**User stories**: I can resize a rotated layer naturally. The opposite side or
corner stays fixed, and the frame follows my pointer rather than drifting.

### What to build

Calculate drag movement in the frame's rotated local coordinate system. Anchor
the opposite handle in document space and derive the new center from that
anchor. Support crossing an axis as a deliberate flip instead of clamping to a
one-pixel box. Apply the same geometry to one layer, multiple layers, and a
selection transform.

### Acceptance criteria

- [x] Rotated corner and edge drags follow the pointer on the frame's local axes.
- [x] The opposite anchor remains fixed within a sub-pixel tolerance.
- [x] Crossing width or height zero produces a predictable horizontal or vertical flip.
- [x] Shift locks the starting aspect ratio.
- [x] Alt/Option scales around the transform center.
- [x] Combined Shift+Alt/Option behavior is deterministic.
- [x] Rotation snaps to 15-degree increments with Shift and remains smooth otherwise.
- [x] Geometry tests cover 0, 45, 90, 135, and arbitrary-degree rotations.

---

## Phase 3: Transform Interaction Polish

**User stories**: Transform behaves like a professional tool on mouse, pen, and
touch. I can see exact values, snap precisely, and never lose a drag at the edge.

### What to build

Use a unified pointer session with pointer capture, live modifiers, and a small
contextual transform readout. Add accurate rotated-frame interior hit testing,
keyboard nudging, frame snapping, and clear Apply/Cancel behavior. Keep the
existing compact Odysseus styling and make the numeric popup a precision surface
rather than a competing transform implementation.

### Acceptance criteria

- [x] Pointer capture keeps a drag alive outside the canvas and browser viewport.
- [x] Clicking inside a rotated frame moves it; clicking its empty bounding-box corner does not.
- [x] Live X, Y, W, H, and angle values stay synchronized with direct manipulation.
- [x] Arrow keys nudge, Shift+Arrow performs a larger nudge, Enter applies, and Escape cancels.
- [x] Layer edges, document center/edges, guides, and grid participate in transform snapping.
- [x] Snap guides clearly identify the active alignment without obscuring the photo.
- [x] A complete gesture creates exactly one undo step.
- [x] Touch gestures do not conflict with viewport pinch/pan behavior.

---

## Phase 4: Transform Content Correctness

**User stories**: Transforming layers never unexpectedly damages masks, text,
group layout, clipping, or image quality. Saving and reopening preserves it.

### What to build

Route raster layers, text layers, linked and unlinked masks, selections, clipped
layers, and grouped multi-selection through the same transform contract. Keep
immutable source data during previews and validate the final result through
undo, cancel, autosave, project download, and reopen.

### Acceptance criteria

- [x] Raster previews are always derived from the session source, never a prior preview.
- [x] Editable text remains editable after scaling, rotation, and flipping.
- [x] Linked masks follow the layer while unlinked masks remain in document space.
- [x] Multi-layer transforms preserve relative centers, order, clipping, and group membership.
- [x] Transforming a selection changes only the selection mask unless content transform is explicitly chosen.
- [x] Apply, Cancel, Undo, Redo, autosave reopen, and project-file reopen produce matching pixels and metadata.
- [x] Large transforms cannot allocate beyond the editor's documented surface budget.

---

## Phase 5: Shared Direct-Manipulation Sessions

**User stories**: Crop, selections, masks, text boxes, and shapes feel consistent
with Transform instead of each behaving like a separate mini application.

### What to build

Generalize the proven transform pointer session into a reusable interaction
contract. Migrate crop and selection movement first as a visible tracer bullet,
including modifiers, snapping, pointer capture, cancel, and one-step history.

### Acceptance criteria

- [x] Transform, crop, and selection movement use the same gesture lifecycle.
- [x] Tool switching safely commits, cancels, or prompts according to one policy.
- [x] No stale pointer session can modify a newly selected tool or document.
- [x] Mouse, pen, and touch event behavior is covered by shared tests.
- [x] Adding a future frame-based tool does not require another global event stack.

---

## Phase 6: Non-Destructive Placed Layers

**User stories**: I can import an image, resize it repeatedly without cumulative
quality loss, replace its source, and choose when to rasterize it.

### What to build

Introduce a placed/smart layer kind containing source pixels and persistent
transform metadata. Import-as-layer uses this kind by default. Rendering applies
the transform at composite time, while Rasterize produces a normal raster layer.

### Acceptance criteria

- [x] Repeated transforms render from the original source rather than resampling the last result.
- [x] A placed layer can be replaced while preserving its transform and masks.
- [x] Rasterize produces a visually matching editable raster layer.
- [x] Masks, clipping, groups, blend modes, and opacity work with placed layers.
- [x] Version migration and recovery handle missing or corrupt placed sources.
- [x] Existing raster projects open without changed output.

---

## Phase 7: Professional Selections And Masks

**User stories**: I can build, inspect, refine, save, transform, and reuse precise
selections without manually repainting every edge.

### What to build

Unify marquee, lasso, wand, SAM, Quick Mask, and saved selections around one
selection-mask model. Add explicit replace/add/subtract/intersect modes, feather,
expand, contract, smooth, border, and a focused refine-edge workflow.

### Acceptance criteria

- [x] Every selection tool supports replace, add, subtract, and intersect modes.
- [x] Feather, expand, contract, smooth, and border preview before applying.
- [x] Quick Mask edits the same canonical selection shown by marching ants.
- [x] Selection-to-layer-mask and layer-mask-to-selection round-trip accurately.
- [x] Saved selections retain names and pixels across reopen.
- [x] Edge refinement works without requiring an AI dependency.

---

## Phase 8: Paint And Retouch Workflow

**User stories**: I can paint and retouch photographs with predictable strokes,
reusable presets, and the controls expected for a mouse, pen, or touch device.

### What to build

Promote brush behavior into a reusable brush engine. Add spacing, smoothing,
pressure mapping, blend mode, sampled color, presets, and stroke preview. Build
healing, dodge, and burn as complete retouching paths using that engine.

### Acceptance criteria

- [x] Brush, eraser, clone, masks, and inpaint share spacing and smoothing behavior.
- [x] Pressure can independently affect size, opacity, or flow when supported.
- [x] Eyedropper samples composite or active-layer color.
- [x] Brush presets can be created, named, selected, and deleted.
- [x] Healing, dodge, and burn create one undo entry per stroke.
- [x] Long strokes remain smooth without blocking the main interface.

---

## Phase 9: Editable Text And Shapes

**User stories**: I can design labels, cards, and overlays with text and vector
shapes that remain editable after saving and reopening.

### What to build

Add on-canvas text-frame editing, selection, caret behavior, typography, and
alignment. Introduce shape layers for rectangle, ellipse, line, and path-backed
polygons with editable fill, stroke, corners, and transform metadata.

### Acceptance criteria

- [x] Text is edited directly on canvas without immediately rasterizing.
- [x] Font, size, weight, line height, letter spacing, alignment, and color persist.
- [x] Rectangle, ellipse, line, and polygon shapes remain editable.
- [x] Shape fill, stroke, width, and corner radius can be changed after creation.
- [x] Text and shape layers support masks, clipping, groups, blend modes, and transform.
- [x] Missing fonts fall back predictably without corrupting the project.

---

## Phase 10: Adjustment Layers And Color

**User stories**: I can correct a photograph non-destructively and return later
to modify the correction without reconstructing the edit.

### What to build

Promote adjustments into first-class layers with masks and clipping. Deliver
Levels and Curves first, then exposure, white balance, hue/saturation, color
balance, selective color, gradients, and channel-aware controls.

### Acceptance criteria

- [ ] Adjustment layers affect content below them and can be clipped or grouped.
- [ ] Every adjustment has live preview, reset, visibility, opacity, mask, Apply, and Cancel behavior.
- [ ] Levels includes histogram, input range, gamma, and output range.
- [ ] Curves supports RGB and channel curves with editable points.
- [ ] Color results match flattened export and project reopen.
- [ ] Large previews are throttled or worker-backed and remain cancellable.

---

## Phase 11: Layer Effects And Filters

**User stories**: I can add common visual effects without permanently altering
the layer and can reorder or disable those effects later.

### What to build

Create an ordered non-destructive filter/effect stack. Begin with Gaussian blur,
sharpen, shadow, stroke, and color overlay; then add filter masks and reusable
effect presets.

### Acceptance criteria

- [ ] Effects can be added, reordered, toggled, edited, masked, and removed.
- [ ] Drop shadow, stroke, color overlay, blur, and sharpen survive project reopen.
- [ ] Effects render correctly inside groups and clipping stacks.
- [ ] Apply/rasterize produces a pixel-equivalent raster result.
- [ ] Expensive filters expose progress and cancellation.

---

## Phase 12: Odysseus Professional Workspace

**User stories**: I can work quickly without fighting floating windows or losing
the active tool, layer, selection, or document context.

### What to build

Refine the existing shell into a consistent professional workspace: contextual
tool options, properties inspector, panel persistence, command search, status
information, multi-document switching, and compact touch sheets. Preserve the
current Odysseus palette, typography, restrained borders, and frosted surfaces.

### Acceptance criteria

- [ ] Tool options appear in one predictable location and never duplicate popup state.
- [ ] Panels remember size, collapsed state, and position per device class.
- [ ] The properties inspector follows the active layer, mask, selection, or tool.
- [ ] Command search exposes actions and shortcuts without adding toolbar clutter.
- [ ] Switching documents preserves independent history, zoom, pan, and selection.
- [ ] Mobile prioritizes canvas area while keeping all commands reachable.

---

## Phase 13: File Interchange And Export

**User stories**: I can bring common assets into Odysseus and export predictable
results without losing transparency, dimensions, or color intent.

### What to build

Strengthen image import/export first, then add layered interchange where a
maintained parser makes it safe. Keep Odysseus project files as the lossless
source of truth and clearly report what an external format cannot preserve.

### Acceptance criteria

- [ ] PNG, JPEG, WebP, and supported modern image imports honor orientation and transparency.
- [ ] Export exposes format, dimensions, quality, metadata, and transparency choices.
- [ ] Copy/paste and drag/drop preserve alpha and use placed layers when appropriate.
- [ ] Layered imports report unsupported features instead of silently flattening them.
- [ ] Exported pixels are covered by deterministic visual comparisons.

---

## Phase 14: Large-Document Performance And Recovery

**User stories**: Large photos and layered projects remain responsive, autosave
reliably, and recover after a crash or interrupted network connection.

### What to build

Move serialization, thumbnails, filters, and suitable pixel operations into
workers. Add dirty-region rendering, reusable surfaces, measurable memory
budgets, operation cancellation, autosave generations, and recovery diagnostics.

### Acceptance criteria

- [ ] Normal interactions remain responsive on the agreed 4K multi-layer benchmark.
- [ ] Compositing avoids rebuilding unaffected layers and thumbnails.
- [ ] History and document surfaces stay within explicit memory limits.
- [ ] Closing or switching documents cancels stale work safely.
- [ ] Autosave never lets an older request overwrite newer state.
- [ ] Recovery can identify the last complete generation and explain skipped data.

---

## Phase 15: Odysseus-Native Assisted Editing

**User stories**: I can use an available local or remote image capability as an
editing assistant while retaining masks, layers, undo, privacy choices, and
normal manual controls.

### What to build

Standardize image capability discovery and requests for generation, editing,
inpainting, segmentation, restoration, and upscaling. Results enter the document
as named layers with provenance and reusable masks. Add orchestration only after
the manual operation it assists is dependable.

### Acceptance criteria

- [ ] The UI describes required capabilities rather than model or provider names.
- [ ] Memory and unrelated chat context are not sent to image endpoints.
- [ ] Requests show progress, support cancellation, and cannot update a closed document.
- [ ] Generated results arrive as reversible layers with prompt/settings metadata.
- [ ] A failed endpoint leaves the source document unchanged and offers a useful retry path.
- [ ] Manual selection and masking remain available when assisted tools are absent.

---

## Phase 16: Professional Release Gate

**User stories**: I can trust the editor for real work and understand what is
unsupported before committing an edit.

### What to build

Create a release gate around complete user journeys rather than isolated button
tests. Cover accessibility, keyboard-only operation, touch, browser differences,
pixel correctness, persistence, failure recovery, and large-document behavior.

### Acceptance criteria

- [ ] Core workflows pass on current Chromium and Firefox desktop builds.
- [ ] Mobile workflows pass at representative phone and tablet viewports.
- [ ] Keyboard-only users can reach every command and escape every modal state.
- [ ] Transform, masks, text, adjustments, export, and reopen have pixel/metadata regression tests.
- [ ] No supported action silently flattens or discards editable document data.
- [ ] The ALPHA badge can be removed based on explicit reliability metrics.

---

## Recommended delivery order

The first four phases are one focused Transform 2.0 program and should ship in
order. Phases 5 and 6 establish the interaction and document foundations needed
for the remaining professional tools. After that, phases 7 through 13 can be
prioritized by user value, while performance and release-gate work continue as
part of every phase rather than being deferred entirely to the end.

The recommended first milestone is complete when Phases 1 through 4 are live:
transforming one layer, multiple layers, text, masks, and selections feels
precise on desktop and mobile and remains correct through undo and reopen.
