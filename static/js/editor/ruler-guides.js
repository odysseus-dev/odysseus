/**
 * Ruler strips + draggable alignment guides for the gallery editor.
 *
 * Rulers: 20px strips at the top and left of the canvas area, drawn on
 * <canvas> elements, showing image-space pixel coordinates with tick marks.
 *
 * Guides: horizontal (dragged from top ruler) or vertical (dragged from
 * left ruler) lines stored in image coordinates. They render as 1px
 * colored overlays spanning the full canvas viewport. Guides can be
 * dragged to reposition, dragged back to the ruler to delete, clicked
 * to select, and removed with the Delete key.
 *
 * Integration points in galleryEditor.js:
 *   const rg = createRulerGuides();
 *   rg.init(canvasOuterEl, canvasAreaEl);
 *   // call rg.update() after zoom / pan changes
 *   // call rg.destroy() in closeEditor
 */
import { state } from './state.js';

const RULER_SIZE  = 20;            // px — ruler strip thickness
const GUIDE_COLOR = '#00c8ff';    // guide line + ruler triangle color
const GUIDE_SEL   = '#ff4d4d';    // selected guide color
const TICK_FG     = 'rgba(160,160,160,0.85)';

export function createRulerGuides() {
  let outerEl      = null;   // ge-canvas-outer
  let canvasAreaEl = null;   // ge-canvas-area (the scroll container)
  let cornerEl     = null;
  let rulerHEl     = null;   // <canvas> horizontal ruler
  let rulerHCtx    = null;
  let rulerVEl     = null;   // <canvas> vertical ruler
  let rulerVCtx    = null;
  let guideLayerEl = null;   // <div> guide overlay (non-scrolling)
  let cleanupFns   = [];

  // ── coordinate helpers ────────────────────────────────────────────────

  /**
   * Returns the canvas element's position relative to the outer wrapper.
   * Using getBoundingClientRect accounts for CSS transform pan and scroll.
   */
  function canvasRelToOuter() {
    if (!state.mainCanvas || !outerEl) return null;
    const or = outerEl.getBoundingClientRect();
    const cr = state.mainCanvas.getBoundingClientRect();
    return {
      left:   cr.left   - or.left,
      top:    cr.top    - or.top,
      right:  cr.right  - or.left,
      bottom: cr.bottom - or.top,
      width:  cr.width,
      height: cr.height,
    };
  }

  /** Area-relative y (from canvas-area top-left) → image pixel y. */
  function areaYToImage(areaY) {
    const r = canvasRelToOuter();
    if (!r || !state.zoom) return 0;
    // Area origin is RULER_SIZE px below outer origin
    return (areaY - (r.top - RULER_SIZE)) / state.zoom;
  }

  /** Area-relative x → image pixel x. */
  function areaXToImage(areaX) {
    const r = canvasRelToOuter();
    if (!r || !state.zoom) return 0;
    return (areaX - (r.left - RULER_SIZE)) / state.zoom;
  }

  /** Image pixel y → y relative to outer origin. */
  function imageYToOuter(imageY) {
    const r = canvasRelToOuter();
    if (!r) return 0;
    return r.top + imageY * state.zoom;
  }

  /** Image pixel x → x relative to outer origin. */
  function imageXToOuter(imageX) {
    const r = canvasRelToOuter();
    if (!r) return 0;
    return r.left + imageX * state.zoom;
  }

  // ── tick interval ─────────────────────────────────────────────────────

  function chooseTick(zoom) {
    const candidates = [1, 2, 5, 10, 20, 25, 50, 100, 200, 250, 500, 1000, 2000];
    for (const t of candidates) {
      if (t * zoom >= 50) return t;
    }
    return 2000;
  }

  // ── ruler drawing ─────────────────────────────────────────────────────

  function drawRulerH() {
    if (!rulerHEl || !outerEl) return;
    const ow = outerEl.clientWidth;
    const oh = RULER_SIZE;
    if (rulerHEl.width !== ow || rulerHEl.height !== oh) {
      rulerHEl.width  = ow;
      rulerHEl.height = oh;
    }
    const ctx = rulerHCtx;
    ctx.clearRect(0, 0, ow, oh);

    const r = canvasRelToOuter();
    if (!r || !state.zoom || !state.imgWidth) return;

    const zoom    = state.zoom;
    const originX = r.left;   // where image pixel 0 maps to in outer coords
    const tick    = chooseTick(zoom);

    ctx.strokeStyle = TICK_FG;
    ctx.fillStyle   = TICK_FG;
    ctx.lineWidth   = 1;
    ctx.font        = '8px sans-serif';
    ctx.textBaseline = 'top';

    // Major ticks every 5 × tick; labels on majors only
    const firstImg = Math.ceil((RULER_SIZE - originX) / zoom / tick) * tick;
    for (let img = firstImg; ; img += tick) {
      const sx = originX + img * zoom;
      if (sx > ow) break;
      if (sx < RULER_SIZE) continue;
      const isMajor = img % (tick * 5) === 0 || tick >= 50;
      const tickH = isMajor ? 10 : 6;
      ctx.beginPath();
      ctx.moveTo(sx, oh - 1);
      ctx.lineTo(sx, oh - tickH);
      ctx.stroke();
      if (isMajor) {
        ctx.fillText(String(img), sx + 2, oh - tickH - 1);
      }
    }

    // Guide triangles on the horizontal ruler (for vertical guides)
    for (const g of (state.guides || [])) {
      if (g.type !== 'v') continue;
      const sx = imageXToOuter(g.pos);
      if (sx < RULER_SIZE || sx > ow) continue;
      ctx.fillStyle = g.id === state.selectedGuideId ? GUIDE_SEL : GUIDE_COLOR;
      ctx.beginPath();
      ctx.moveTo(sx,     oh);
      ctx.lineTo(sx - 4, oh - 9);
      ctx.lineTo(sx + 4, oh - 9);
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = TICK_FG;
    }
  }

  function drawRulerV() {
    if (!rulerVEl || !outerEl) return;
    const ow = RULER_SIZE;
    const oh = outerEl.clientHeight;
    if (rulerVEl.width !== ow || rulerVEl.height !== oh) {
      rulerVEl.width  = ow;
      rulerVEl.height = oh;
    }
    const ctx = rulerVCtx;
    ctx.clearRect(0, 0, ow, oh);

    const r = canvasRelToOuter();
    if (!r || !state.zoom || !state.imgHeight) return;

    const zoom    = state.zoom;
    const originY = r.top;
    const tick    = chooseTick(zoom);

    ctx.strokeStyle = TICK_FG;
    ctx.fillStyle   = TICK_FG;
    ctx.lineWidth   = 1;
    ctx.font        = '8px sans-serif';
    ctx.textBaseline = 'top';

    const firstImg = Math.ceil((RULER_SIZE - originY) / zoom / tick) * tick;
    for (let img = firstImg; ; img += tick) {
      const sy = originY + img * zoom;
      if (sy > oh) break;
      if (sy < RULER_SIZE) continue;
      const isMajor = img % (tick * 5) === 0 || tick >= 50;
      const tickW = isMajor ? 10 : 6;
      ctx.beginPath();
      ctx.moveTo(ow - 1, sy);
      ctx.lineTo(ow - tickW, sy);
      ctx.stroke();
      if (isMajor) {
        ctx.save();
        ctx.translate(ow - tickW - 1, sy - 2);
        ctx.rotate(-Math.PI / 2);
        ctx.fillText(String(img), 0, 0);
        ctx.restore();
      }
    }

    // Guide triangles on the vertical ruler (for horizontal guides)
    for (const g of (state.guides || [])) {
      if (g.type !== 'h') continue;
      const sy = imageYToOuter(g.pos);
      if (sy < RULER_SIZE || sy > oh) continue;
      ctx.fillStyle = g.id === state.selectedGuideId ? GUIDE_SEL : GUIDE_COLOR;
      ctx.beginPath();
      ctx.moveTo(ow,     sy);
      ctx.lineTo(ow - 9, sy - 4);
      ctx.lineTo(ow - 9, sy + 4);
      ctx.closePath();
      ctx.fill();
      ctx.fillStyle = TICK_FG;
    }
  }

  // ── guide line DOM ────────────────────────────────────────────────────

  function buildGuideDom(guide) {
    const wrap = document.createElement('div');
    wrap.className = guide.type === 'h' ? 'ge-guide-h-wrap' : 'ge-guide-v-wrap';
    wrap.dataset.guideId = String(guide.id);

    const line = document.createElement('div');
    line.className = 'ge-guide-line';
    wrap.appendChild(line);

    wrap.addEventListener('pointerdown', (e) => {
      e.stopPropagation();
      selectGuide(guide.id);
      startGuideReposition(e, guide);
    });

    guideLayerEl.appendChild(wrap);
    return wrap;
  }

  function positionGuide(wrap, guide) {
    const isH   = guide.type === 'h';
    const isSel = guide.id === state.selectedGuideId;

    wrap.className = (isH ? 'ge-guide-h-wrap' : 'ge-guide-v-wrap')
                   + (isSel ? ' ge-guide-selected' : '');

    const r = canvasRelToOuter();
    if (!r) return;

    if (isH) {
      const y = imageYToOuter(guide.pos) - RULER_SIZE;
      wrap.style.top  = y + 'px';
    } else {
      const x = imageXToOuter(guide.pos) - RULER_SIZE;
      wrap.style.left = x + 'px';
    }
  }

  function updateGuidePositions() {
    if (!guideLayerEl) return;
    const guides = state.guides || [];
    const ids    = new Set(guides.map(g => String(g.id)));

    // Remove stale
    guideLayerEl.querySelectorAll('[data-guide-id]').forEach(el => {
      if (!ids.has(el.dataset.guideId)) el.remove();
    });

    // Add / reposition
    for (const g of guides) {
      let el = guideLayerEl.querySelector(`[data-guide-id="${g.id}"]`);
      if (!el) el = buildGuideDom(g);
      positionGuide(el, g);
    }
  }

  // ── guide reposition drag ─────────────────────────────────────────────

  function startGuideReposition(e, guide) {
    const or = outerEl.getBoundingClientRect();

    const onMove = (me) => {
      const outerY = me.clientY - or.top;
      const outerX = me.clientX - or.left;
      if (guide.type === 'h') {
        guide.pos = Math.round(areaYToImage(outerY - RULER_SIZE));
      } else {
        guide.pos = Math.round(areaXToImage(outerX - RULER_SIZE));
      }
      updateGuidePositions();
      drawRulerH();
      drawRulerV();
    };

    const onUp = (me) => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup',   onUp);

      const outerY = me.clientY - or.top;
      const outerX = me.clientX - or.left;

      // Dragging back into the ruler strip deletes the guide (Photoshop behavior)
      if ((guide.type === 'h' && outerY < RULER_SIZE) ||
          (guide.type === 'v' && outerX < RULER_SIZE)) {
        removeGuide(guide.id);
      }
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup',   onUp);
  }

  // ── creation drag (from ruler) ────────────────────────────────────────

  function startCreationDrag(type, e) {
    const or = outerEl.getBoundingClientRect();

    // Ghost line shown while dragging (wrapper + inner line)
    const ghost = document.createElement('div');
    ghost.className = type === 'h' ? 'ge-guide-h-wrap ge-guide-ghost' : 'ge-guide-v-wrap ge-guide-ghost';
    ghost.style.display = 'none';
    const ghostLine = document.createElement('div');
    ghostLine.className = 'ge-guide-line';
    ghost.appendChild(ghostLine);
    guideLayerEl.appendChild(ghost);

    const onMove = (me) => {
      const outerX = me.clientX - or.left;
      const outerY = me.clientY - or.top;
      if (type === 'h') {
        const inCanvas = outerY > RULER_SIZE;
        ghost.style.display = inCanvas ? '' : 'none';
        ghost.style.top  = (outerY - RULER_SIZE) + 'px';
      } else {
        const inCanvas = outerX > RULER_SIZE;
        ghost.style.display = inCanvas ? '' : 'none';
        ghost.style.left = (outerX - RULER_SIZE) + 'px';
      }
    };

    const onUp = (me) => {
      window.removeEventListener('pointermove', onMove);
      window.removeEventListener('pointerup',   onUp);
      ghost.remove();

      const outerX = me.clientX - or.left;
      const outerY = me.clientY - or.top;

      // Only commit if released inside the canvas area (past the ruler strip)
      if (type === 'h' && outerY > RULER_SIZE) {
        addGuide('h', Math.round(areaYToImage(outerY - RULER_SIZE)));
      } else if (type === 'v' && outerX > RULER_SIZE) {
        addGuide('v', Math.round(areaXToImage(outerX - RULER_SIZE)));
      }
    };

    window.addEventListener('pointermove', onMove);
    window.addEventListener('pointerup',   onUp);
    onMove(e);
  }

  // ── guide management ──────────────────────────────────────────────────

  function addGuide(type, pos) {
    if (!Array.isArray(state.guides)) state.guides = [];
    if (!state.nextGuideId) state.nextGuideId = 1;
    const guide = { id: state.nextGuideId++, type, pos };
    state.guides.push(guide);
    selectGuide(guide.id, false);
    update();
  }

  function removeGuide(id) {
    if (!state.guides) return;
    state.guides = state.guides.filter(g => g.id !== id);
    if (state.selectedGuideId === id) state.selectedGuideId = null;
    updateGuidePositions();
    drawRulerH();
    drawRulerV();
  }

  function selectGuide(id, redraw = true) {
    state.selectedGuideId = id;
    if (redraw) {
      updateGuidePositions();
      drawRulerH();
      drawRulerV();
    }
  }

  function deselectAll() {
    if (state.selectedGuideId == null) return;
    state.selectedGuideId = null;
    updateGuidePositions();
    drawRulerH();
    drawRulerV();
  }

  // ── event handlers ────────────────────────────────────────────────────

  function onRulerHPointerDown(e) {
    e.preventDefault();
    startCreationDrag('h', e);
  }

  function onRulerVPointerDown(e) {
    e.preventDefault();
    startCreationDrag('v', e);
  }

  function onKeyDown(e) {
    if (state.selectedGuideId == null) return;
    const tag = document.activeElement?.tagName?.toLowerCase();
    if (tag === 'input' || tag === 'textarea') return;
    if (e.key === 'Delete' || e.key === 'Backspace') {
      e.preventDefault();
      removeGuide(state.selectedGuideId);
    } else if (e.key === 'Escape') {
      deselectAll();
    }
  }

  function onOuterPointerDown(e) {
    // Click on ruler or the canvas area (not on a guide) → deselect
    if (e.target.closest('[data-guide-id]')) return;
    deselectAll();
  }

  // ── public API ────────────────────────────────────────────────────────

  function update() {
    drawRulerH();
    drawRulerV();
    updateGuidePositions();
  }

  function init(outerElement, areaElement) {
    outerEl      = outerElement;
    canvasAreaEl = areaElement;

    // Corner piece (covers the 20×20 intersection of both rulers)
    cornerEl = document.createElement('div');
    cornerEl.className = 'ge-ruler-corner';
    outerEl.appendChild(cornerEl);

    // Horizontal ruler (top strip)
    rulerHEl  = document.createElement('canvas');
    rulerHEl.className = 'ge-ruler-h';
    rulerHCtx = rulerHEl.getContext('2d');
    outerEl.appendChild(rulerHEl);

    // Vertical ruler (left strip)
    rulerVEl  = document.createElement('canvas');
    rulerVEl.className = 'ge-ruler-v';
    rulerVCtx = rulerVEl.getContext('2d');
    outerEl.appendChild(rulerVEl);

    // Guide overlay (sits over the canvas area, same inset as canvas area)
    guideLayerEl = document.createElement('div');
    guideLayerEl.className = 'ge-guide-layer';
    outerEl.appendChild(guideLayerEl);

    // Init state arrays if not present
    if (!Array.isArray(state.guides))    state.guides = [];
    if (state.nextGuideId == null)       state.nextGuideId = 1;
    if (state.selectedGuideId === undefined) state.selectedGuideId = null;

    const hDown = (e) => onRulerHPointerDown(e);
    const vDown = (e) => onRulerVPointerDown(e);
    const kDown = (e) => onKeyDown(e);
    const oDown = (e) => onOuterPointerDown(e);

    rulerHEl.addEventListener('pointerdown', hDown);
    rulerVEl.addEventListener('pointerdown', vDown);
    document.addEventListener('keydown',     kDown);
    outerEl.addEventListener('pointerdown',  oDown);

    cleanupFns = [
      () => rulerHEl?.removeEventListener('pointerdown', hDown),
      () => rulerVEl?.removeEventListener('pointerdown', vDown),
      () => document.removeEventListener('keydown',     kDown),
      () => outerEl?.removeEventListener('pointerdown',  oDown),
    ];

    update();
  }

  function destroy() {
    cleanupFns.forEach(fn => fn());
    cleanupFns   = [];
    cornerEl     = rulerHEl = rulerVEl = guideLayerEl = null;
    outerEl      = canvasAreaEl = null;
    rulerHCtx    = rulerVCtx = null;
  }

  return { init, update, destroy };
}
