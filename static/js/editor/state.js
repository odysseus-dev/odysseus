/**
 * Editor state store — a single mutable object that the gallery editor
 * and its tool modules read and write directly.
 *
 * Migration: galleryEditor.js used to own ~110 module-scope `let`
 * declarations and capture them via closure. Tool modules can't import
 * a `let` binding's mutations across module boundaries, so we move the
 * state into a single exported OBJECT whose properties are freely
 * mutated by anyone holding a reference. Read/write `state.transformW`
 * exactly the way the old code wrote `_transformW`.
 *
 * Slices land here one tool at a time; this file grows as more state
 * migrates out of galleryEditor.js. Defaults match the legacy
 * module-scope initializers verbatim — every `state.foo = …` reset
 * site in galleryEditor.js still works unchanged.
 */
export const state = {
  // ── Transform tool ──
  // Drag-resize / rotate session state. While `transformActive` is
  // false every field below should be considered stale.
  transformActive: false,
  transformLayer: null,
  transformOrigW: 0,
  transformOrigH: 0,
  // Which corner/edge handle the user is currently dragging. One of
  // 'tl' | 'tr' | 'bl' | 'br' | 'rot' | null.
  transformHandle: null,
  // Which handle is currently under the cursor (no drag). Drives the
  // hover cursor lookup; lives next to `transformHandle` because both
  // come from `_getTransformHandle`.
  hoveredHandle: null,
  // Snapshot of the layer canvas + offset at transform start so Cancel
  // can restore exactly without re-fetching from the layer.
  transformOrigCanvas: null,
  transformOrigOffset: null,
  // In-progress dimensions / rotation / flips committed on Apply.
  transformPendingW: 0,
  transformPendingH: 0,
  transformPendingRot: 0,
  transformPendingFlipH: false,
  transformPendingFlipV: false,
  transformAspectLock: true,
  // Floating Transform popup element + drag-start offsets.
  transformPopup: null,
  transformStartX: 0,
  transformStartY: 0,
  transformStartOffX: 0,
  transformStartOffY: 0,
  // Transform overlay canvas — separate canvas positioned over the
  // main canvas with extra slack for handle rendering. Created by
  // _buildEditor; the move/transform tools draw their handle layer
  // onto its 2D context.
  transformOverlay: null,
  transformOverlayCtx: null,

  // ── Magic Wand tool ──
  // Binary selection mask + the layer it was sampled from. `wandMask`
  // is a canvas the size of `wandLayer`'s pixels, white where selected,
  // transparent elsewhere. `wandLastSeed` remembers the last click so
  // tolerance retunes can re-run the flood-fill without re-prompting.
  wandMask: null,
  wandLayerId: null,
  wandTolerance: 24,
  wandMaskVisible: true,
  wandMode: 'replace',
  wandLiveRetune: false,
  wandLastSeed: null,
  // Cached layer pixel data (getImageData is O(pixels) — expensive for
  // 4K layers; invalidated when the active layer changes).
  wandSrcCache: null,

  // ── Brush / Eraser / Clone tools ──
  // Shared paint color (brush picks up the swatch; eraser and clone
  // ignore color but reuse the same picker control).
  color: '#e06c75',
  // Brush diameter in canvas pixels. Persisted across tool switches;
  // bumped to a mask-friendly default on first inpaint entry.
  brushSize: 8,
  // Per-tool stroke modifiers — opacity + flow + softness. Each tool
  // owns its own row so users can dial them in independently.
  brushOpacity: 100,
  brushFlow: 100,
  brushSoftness: 100,
  eraserOpacity: 100,
  eraserFlow: 100,
  eraserSoftness: 100,
  cloneOpacity: 100,
  cloneFlow: 100,
  cloneSoftness: 100,
  // Clone-stamp source point (set via Alt-click or double-tap). Null
  // means no source picked yet — clicking with the clone tool no-ops
  // until a source is set.
  cloneSourceX: null,
  cloneSourceY: null,
  // Stroke-start offsets so the source moves WITH the brush, keeping
  // the source→destination offset constant across the stroke.
  cloneStrokeStartX: null,
  cloneStrokeStartY: null,
  // Frozen snapshot of the source layer's pixels at stroke start so
  // moving the source over previously-painted pixels samples the
  // original, not the in-progress stamp ring.
  cloneSourceSnapshot: null,
  cloneSourceLayerId: null,
  // Mobile: double-tap detection for "set source" since Alt-click
  // isn't an option without a keyboard.
  cloneLastTapTime: 0,
  cloneLastTapX: 0,
  cloneLastTapY: 0,

  // ── Inpaint + mask ──
  // Active mask canvas + its 2D context. Re-pointed to the active
  // mask sub-layer whenever the user picks a different mask in the
  // layer panel.
  maskCanvas: null,
  maskCtx: null,
  maskVisible: true,
  // Reused canvas for the union-of-masks tint pass (saves repeated
  // allocation on every composite).
  compositeMaskUnion: null,
  // Visual tint applied to mask pixels in the composite — purely
  // cosmetic; the AI model still sees a hard binary mask.
  maskTintColor: 'rgba(255, 110, 110, 1)',
  maskTintOpacity: 0.28,
  // Inpaint-tool paint vs erase modes (Ctrl+Alt flips for a single
  // stroke; UI buttons toggle the persistent setting).
  inpaintEraseMode: false,
  inpaintEraseStroke: false,
  // First-entry guard: bump brush size to the mask-friendly default
  // the first time the user opens inpaint per session.
  inpaintBrushInitialised: false,
  // Last successful inpaint result layer — drives the live edge
  // feather / stroke sliders (those only apply to the most recent
  // result).
  lastInpaintLayerId: null,
  // Captured handlers so we can detach them on close without leaking.
  inpaintDismissHandlers: null,
  // Background-remove tool state — pristine snapshot so the edge
  // cleanup sliders can live-rebuild alpha without re-running rembg.
  rembgLiveLayer: null,
  rembgLiveSnap: null,
  // User-painted background sample strokes. The remove-bg request sends
  // this as a separate mask so the backend can remove matching pixels.
  rembgPipeline: 'auto',
  rembgSampleCanvas: null,
  rembgSampleCtx: null,
  rembgSampleVisible: true,
  // Memoised "is rembg installed on the server?" probe.
  rembgInstalledCache: null,
  rembgAvailabilityListenerWired: false,

  // ── Stroke drag state ──
  // Generic in-progress-stroke flags shared by brush/eraser/clone/
  // inpaint. `lastX/Y` are the last mouse position used to interpolate
  // a continuous line through fast-moving cursor samples.
  drawing: false,
  lastX: 0,
  lastY: 0,

  // ── Move tool ──
  moving: false,
  moveStartX: 0,
  moveStartY: 0,
  // Layer offset at drag start so we can compute the new offset by
  // (mouse - startMouse) + startOffset rather than accumulating delta.
  moveLayerOffsetX: 0,
  moveLayerOffsetY: 0,
  // Snap guides drawn during a move-tool drag (Ctrl held). Each entry
  // is a vertical / horizontal line in canvas space.
  activeSnapGuides: null,

  // ── Crop tool ──
  cropping: false,
  cropStart: null,
  cropEnd: null,
  cropRect: null,
  cropAspectLock: null,
  // True while the user drags the inside of an already-finished crop
  // rect to reposition it.
  cropMoving: false,
  cropMoveStart: null,

  // ── Lasso tool ──
  // Freehand selection polygon in canvas pixels. Empty when no lasso
  // is in progress or staged.
  lassoPoints: [],
  lassoActive: false,

  // In-editor copy/paste — separate from the OS clipboard so we can
  // round-trip layer alpha and metadata losslessly.
  internalClipboard: null,

  // ── Editor DOM refs ──
  // Root container that openEditor mounts into.
  container: null,
  // Main image canvas + its 2D context. Re-created on every openEditor
  // so the editor can reopen with fresh dimensions.
  mainCanvas: null,
  mainCtx: null,

  // ── Document + layers ──
  layers: [],
  activeLayerId: null,
  // Active tool ID — one of move/crop/transform/brush/eraser/clone/
  // lasso/wand/inpaint/rembg/harmonize/sharpen/upscale/style.
  tool: 'move',
  // Display zoom (1 = 100%). pan{X,Y} translate the canvas inside the
  // viewport.
  zoom: 1,
  panX: 0,
  panY: 0,
  // Document dimensions in canvas pixels.
  imgWidth: 0,
  imgHeight: 0,
  // Gallery image id this editor session is editing, or null for
  // blank-canvas drafts.
  imageId: null,
  // Original file extension so save-over-original re-encodes in the
  // same format (JPEG vs PNG matters: JPEG cuts upload size 5-10× for
  // camera photos over remote tunnels).
  originalExt: 'png',
  // True between openEditor / closeEditor — guards async callbacks
  // that fire after the user closes the editor (don't draw onto a
  // dead canvas, don't re-mount the spinner).
  editorOpen: false,
  // Document-level click-away handlers registered for the current
  // session. Tracked so closeEditor can detach them all cleanly.
  // Mutated in place (push / length = 0); the reference never changes.
  editorDocClickHandlers: [],
  // Generic per-editor cleanup callbacks for window/viewport listeners
  // installed by submodules during the current openEditor() session.
  editorCleanupHandlers: [],

  // ── Undo / redo ──
  undoStack: [],
  redoStack: [],

  // ── Layer offsets + id allocation ──
  // Map<layerId, {x, y}> — kept in a Map so we can serialise it
  // separately from the layer's own canvas. Mutated in place.
  layerOffsets: new Map(),
  nextLayerId: 1,

  // ── Popup / panel handles ──
  fxPopupEl: null,
  fxPopupLayerId: null,
  fxMenuEl: null,
  adjPopupEl: null,
  // rAF-throttled live preview while sliders are dragged in adj popups.
  adjRafPending: false,
  historyPanelEl: null,
  // Custom brush-cursor overlay element (circle following the mouse).
  cursorEl: null,
  // Hover-preview thumbnail floating element (singleton, repositioned).
  layerThumbEl: null,
  // Loading-overlay element (whirlpool + label).
  editorLoadingEl: null,

  // ── Draft persistence ──
  draftId: null,
  draftName: '',
  persistTimer: null,
  // Current PUT/POST promise so concurrent saves can chain.
  persistInFlight: null,
  // True when an edit happened during an in-flight save — triggers a
  // follow-up persist after the current one finishes.
  persistDirty: false,
};
