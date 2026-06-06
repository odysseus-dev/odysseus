// static/js/signature.js
//
// Reusable signature module. Two entry points:
//   capture(opts)  — open a drawing modal, return Promise<Signature|null>
//   pick(opts)     — show saved signatures + a "new" tile, return Promise<Signature|null>
//
// Signature shape: { id, dataUrl, width, height, name }
//
// Drawing uses a per-stroke smoother: each quadratic-bezier segment is anchored
// at the previous point with control points at midpoints, which yields a
// Catmull-Rom-like curve without external deps. Variable stroke width is
// derived from pointer velocity (slower → thicker), which gives signatures
// their characteristic ink-bleed feel.

const API_BASE = window.location.origin;

function _esc(s) {
  return String(s ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

function _safeSignatureDataUrl(raw) {
  const value = String(raw || '').trim();
  return /^data:image\/(?:png|jpe?g);base64,[a-z0-9+/=\s]+$/i.test(value) ? value : '';
}

// Last signature the user picked or created in this session. Lets the export
// modal pre-fill subsequent signature fields with the same one — sign once,
// applies everywhere.
let _lastUsed = null;
export function getLastUsed() { return _lastUsed; }
export function setLastUsed(sig) { _lastUsed = sig || null; }

class SmoothPad {
  constructor(canvas, opts = {}) {
    this.canvas = canvas;
    this.ctx = canvas.getContext('2d');
    this.minWidth = opts.minWidth ?? 1.0;
    this.maxWidth = opts.maxWidth ?? 3.0;
    this.color = opts.color ?? '#111';
    this.bgColor = opts.bgColor ?? '#fff';
    // Heavy-smoothing knobs (much smoother than v1):
    //  - minDistance: jitter floor (px) — bigger = more aggressive thinning
    //  - emaAlpha: live EMA on incoming points (lower = smoother, laggier)
    //  - chaikinIters: Chaikin corner-cutting passes on stroke-end redraw
    //    (each pass roughly doubles segment count and rounds every corner;
    //    3 passes is silky, 4 is glass)
    //  - tension: secondary Catmull-Rom tightness for the final cubic curve
    this.minDistance = opts.minDistance ?? 5.0;
    this.emaAlpha = opts.emaAlpha ?? 0.15;
    this.chaikinIters = opts.chaikinIters ?? 4;
    this.tension = opts.tension ?? 0.35;
    this._strokes = [];   // array of { points: [{x,y,t,p}] }
    this._current = null;
    this._currentSmoothed = null; // EMA point used for live drawing
    this._isEmpty = true;
    this._wirePointer();
    this.clear();
  }

  _wirePointer() {
    const c = this.canvas;
    c.style.touchAction = 'none';
    c.addEventListener('pointerdown', (e) => this._onDown(e));
    c.addEventListener('pointermove', (e) => this._onMove(e));
    c.addEventListener('pointerup',   (e) => this._onUp(e));
    c.addEventListener('pointercancel', (e) => this._onUp(e));
    c.addEventListener('pointerleave', (e) => this._onUp(e));
  }

  _toLocal(e) {
    const r = this.canvas.getBoundingClientRect();
    const sx = this.canvas.width / r.width;
    const sy = this.canvas.height / r.height;
    return {
      x: (e.clientX - r.left) * sx,
      y: (e.clientY - r.top) * sy,
      t: performance.now(),
      p: e.pressure && e.pressure > 0 ? e.pressure : 0.5,
    };
  }

  _onDown(e) {
    e.preventDefault();
    this.canvas.setPointerCapture(e.pointerId);
    const raw = this._toLocal(e);
    this._current = { points: [raw] };
    this._currentSmoothed = { ...raw };
    this._strokes.push(this._current);
    this._isEmpty = false;
  }

  _onMove(e) {
    if (!this._current) return;
    e.preventDefault();
    const raw = this._toLocal(e);
    const pts = this._current.points;
    const last = pts[pts.length - 1];
    if (Math.hypot(raw.x - last.x, raw.y - last.y) < this.minDistance) return;
    // EMA the incoming point against the running smoothed point so the live
    // preview already reads as smooth (the final stroke-end pass will smooth
    // further via Catmull-Rom).
    const a = this.emaAlpha;
    const sm = this._currentSmoothed;
    const smoothed = {
      x: sm.x + a * (raw.x - sm.x),
      y: sm.y + a * (raw.y - sm.y),
      t: raw.t,
      p: sm.p + a * (raw.p - sm.p),
    };
    this._currentSmoothed = smoothed;
    pts.push(smoothed);
    if (pts.length >= 3) {
      this._drawSegment(pts[pts.length - 3], pts[pts.length - 2], pts[pts.length - 1]);
    } else if (pts.length === 2) {
      const [a0, b0] = pts;
      this._strokeLine(a0, b0, this._widthBetween(a0, b0));
    }
  }

  _onUp(e) {
    if (!this._current) return;
    try { this.canvas.releasePointerCapture(e.pointerId); } catch (_) {}
    // Heavy-smoothing finalize: redraw the just-finished stroke as a
    // Catmull-Rom-fit cubic (much smoother than incremental quadratics).
    const stroke = this._current;
    this._current = null;
    this._currentSmoothed = null;
    if (stroke && stroke.points.length >= 2) {
      this._repaintAll();
    }
  }

  _repaintAll() {
    const ctx = this.ctx;
    ctx.fillStyle = this.bgColor;
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    for (const stroke of this._strokes) {
      this._drawStrokeSmooth(stroke.points);
    }
  }

  _chaikinPass(pts) {
    // Each interior pair (a,b) becomes two new points at 1/4 and 3/4 along
    // the segment. Endpoints preserved.
    if (pts.length < 3) return pts;
    const out = [pts[0]];
    for (let i = 0; i < pts.length - 1; i++) {
      const a = pts[i], b = pts[i + 1];
      out.push({
        x: a.x * 0.75 + b.x * 0.25,
        y: a.y * 0.75 + b.y * 0.25,
        t: a.t, p: (a.p + b.p) / 2,
      });
      out.push({
        x: a.x * 0.25 + b.x * 0.75,
        y: a.y * 0.25 + b.y * 0.75,
        t: b.t, p: (a.p + b.p) / 2,
      });
    }
    out.push(pts[pts.length - 1]);
    return out;
  }

  _drawStrokeSmooth(pts) {
    if (!pts || pts.length < 2) return;
    if (pts.length === 2) {
      this._strokeLine(pts[0], pts[1], this._widthBetween(pts[0], pts[1]));
      return;
    }
    // Aggressive smoothing: N passes of Chaikin's corner-cutting, then a
    // Catmull-Rom cubic through the densified points.
    let smoothed = pts;
    for (let i = 0; i < this.chaikinIters; i++) {
      smoothed = this._chaikinPass(smoothed);
    }
    const ctx = this.ctx;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = this.color;
    const t = Math.max(0.05, Math.min(1, this.tension));
    const padded = [smoothed[0], ...smoothed, smoothed[smoothed.length - 1]];
    for (let i = 1; i < padded.length - 2; i++) {
      const p0 = padded[i - 1];
      const p1 = padded[i];
      const p2 = padded[i + 1];
      const p3 = padded[i + 2];
      const c1 = {
        x: p1.x + ((p2.x - p0.x) / 6) * t * 3,
        y: p1.y + ((p2.y - p0.y) / 6) * t * 3,
      };
      const c2 = {
        x: p2.x - ((p3.x - p1.x) / 6) * t * 3,
        y: p2.y - ((p3.y - p1.y) / 6) * t * 3,
      };
      const w = this._widthBetween(p1, p2);
      ctx.lineWidth = w;
      ctx.beginPath();
      ctx.moveTo(p1.x, p1.y);
      ctx.bezierCurveTo(c1.x, c1.y, c2.x, c2.y, p2.x, p2.y);
      ctx.stroke();
    }
  }

  _widthBetween(a, b) {
    const dt = Math.max(1, b.t - a.t);
    const dist = Math.hypot(b.x - a.x, b.y - a.y);
    const v = dist / dt;          // px/ms
    // Slower strokes → thicker line. Tunable mapping.
    const k = 1 / (1 + v * 4);
    let w = this.minWidth + (this.maxWidth - this.minWidth) * k;
    // Stylus pressure further modulates
    const pAvg = (a.p + b.p) / 2;
    w *= 0.6 + 0.8 * pAvg;
    return Math.max(this.minWidth * 0.8, Math.min(this.maxWidth * 1.2, w));
  }

  _strokeLine(a, b, width) {
    const ctx = this.ctx;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = this.color;
    ctx.lineWidth = width;
    ctx.beginPath();
    ctx.moveTo(a.x, a.y);
    ctx.lineTo(b.x, b.y);
    ctx.stroke();
  }

  _drawSegment(p0, p1, p2) {
    // Quadratic bezier from mid(p0,p1) to mid(p1,p2) with control p1.
    const m1 = { x: (p0.x + p1.x) / 2, y: (p0.y + p1.y) / 2 };
    const m2 = { x: (p1.x + p2.x) / 2, y: (p1.y + p2.y) / 2 };
    const w = this._widthBetween(p1, p2);
    const ctx = this.ctx;
    ctx.lineCap = 'round';
    ctx.lineJoin = 'round';
    ctx.strokeStyle = this.color;
    ctx.lineWidth = w;
    ctx.beginPath();
    ctx.moveTo(m1.x, m1.y);
    ctx.quadraticCurveTo(p1.x, p1.y, m2.x, m2.y);
    ctx.stroke();
  }

  clear() {
    const ctx = this.ctx;
    ctx.save();
    ctx.fillStyle = this.bgColor;
    ctx.fillRect(0, 0, this.canvas.width, this.canvas.height);
    ctx.restore();
    this._strokes = [];
    this._current = null;
    this._isEmpty = true;
  }

  undo() {
    if (!this._strokes.length) return;
    this._strokes.pop();
    this._repaintAll();
    this._isEmpty = this._strokes.length === 0;
  }

  isEmpty() { return this._isEmpty; }

  // Trim transparent border so the saved signature isn't surrounded by
  // empty space when stamped into a PDF rect.
  toTrimmedDataUrl(padding = 4) {
    const cw = this.canvas.width;
    const ch = this.canvas.height;
    const ctx = this.ctx;
    const data = ctx.getImageData(0, 0, cw, ch).data;
    let minX = cw, minY = ch, maxX = -1, maxY = -1;
    // bgColor is opaque white — detect non-white pixels
    for (let y = 0; y < ch; y++) {
      for (let x = 0; x < cw; x++) {
        const i = (y * cw + x) * 4;
        if (data[i] < 250 || data[i + 1] < 250 || data[i + 2] < 250) {
          if (x < minX) minX = x;
          if (x > maxX) maxX = x;
          if (y < minY) minY = y;
          if (y > maxY) maxY = y;
        }
      }
    }
    if (maxX < 0) {
      // empty
      return { dataUrl: this.canvas.toDataURL('image/png'), width: cw, height: ch };
    }
    minX = Math.max(0, minX - padding);
    minY = Math.max(0, minY - padding);
    maxX = Math.min(cw - 1, maxX + padding);
    maxY = Math.min(ch - 1, maxY + padding);
    const w = maxX - minX + 1;
    const h = maxY - minY + 1;
    const off = document.createElement('canvas');
    off.width = w; off.height = h;
    const octx = off.getContext('2d');
    // Transparent background so the signature blends with the PDF
    octx.drawImage(this.canvas, minX, minY, w, h, 0, 0, w, h);
    // Replace white with transparent
    const id = octx.getImageData(0, 0, w, h);
    const px = id.data;
    for (let i = 0; i < px.length; i += 4) {
      if (px[i] > 245 && px[i + 1] > 245 && px[i + 2] > 245) {
        px[i + 3] = 0;
      }
    }
    octx.putImageData(id, 0, 0);
    return { dataUrl: off.toDataURL('image/png'), width: w, height: h };
  }
}

function _modal(innerHtml) {
  // Match the app's standard .modal pattern (defined in static/style.css).
  const overlay = document.createElement('div');
  overlay.className = 'modal sig-modal-overlay';
  overlay.style.cssText = 'pointer-events:auto;background:rgba(0,0,0,0.45);z-index:10100;';
  overlay.innerHTML = innerHtml;
  document.body.appendChild(overlay);
  return overlay;
}

async function _listSignatures() {
  const r = await fetch(`${API_BASE}/api/signatures`);
  if (!r.ok) return [];
  const data = await r.json();
  return data.signatures || [];
}

async function _saveSignature({ dataUrl, width, height, name }) {
  const r = await fetch(`${API_BASE}/api/signatures`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ data: dataUrl, width, height, name }),
  });
  if (!r.ok) {
    const t = await r.text();
    throw new Error(t || r.statusText);
  }
  return await r.json();
}

async function _deleteSignature(id) {
  await fetch(`${API_BASE}/api/signatures/${id}`, { method: 'DELETE' });
}

// Smoothness slider maps a single 0–10 value to the two main knobs.
// Persisted in localStorage so the user's preference sticks.
const SMOOTH_KEY = 'odysseus.signature.smoothness';
function _loadSmoothness() {
  const v = parseInt(localStorage.getItem(SMOOTH_KEY) || '', 10);
  return Number.isFinite(v) && v >= 0 && v <= 10 ? v : 7;
}
function _saveSmoothness(v) {
  try { localStorage.setItem(SMOOTH_KEY, String(v)); } catch (_) {}
}
function _smoothnessToParams(v) {
  // 0 = raw, 10 = extreme glass
  return {
    minDistance: 1.0 + v * 0.6,        // 1.0 → 7.0
    emaAlpha: Math.max(0.05, 0.6 - v * 0.055), // 0.6 → 0.05
    chaikinIters: Math.round(v * 0.6), // 0 → 6
  };
}
function _applySmoothness(pad, v) {
  const p = _smoothnessToParams(v);
  pad.minDistance = p.minDistance;
  pad.emaAlpha = p.emaAlpha;
  pad.chaikinIters = p.chaikinIters;
  pad._repaintAll();
}

// ── capture: open the drawing modal, save on confirm ─────────────────────
export function capture(opts = {}) {
  return new Promise((resolve) => {
    const initialSmooth = _loadSmoothness();
    const overlay = _modal(`
      <div class="modal-content" style="width:min(560px,94vw);">
        <div class="modal-header">
          <h4>Draw your signature</h4>
          <button class="sig-close modal-close" title="Close">×</button>
        </div>
        <div class="modal-body">
          <canvas class="sig-canvas" width="900" height="280" data-no-swipe-dismiss></canvas>
          <div style="margin-top:10px;display:flex;align-items:center;gap:10px;font-size:0.78rem;">
            <label for="sig-smoothness" style="white-space:nowrap;opacity:0.8;">Smoothness</label>
            <input id="sig-smoothness" class="sig-smoothness" type="range" min="0" max="10" step="1" value="${initialSmooth}" style="flex:1;">
            <span class="sig-smoothness-val" style="width:18px;text-align:right;font-variant-numeric:tabular-nums;opacity:0.7;">${initialSmooth}</span>
          </div>
          <input class="sig-name" type="text" placeholder="Name (optional, e.g. 'Full' or 'Initials')" style="margin-top:10px;">
        </div>
        <div class="modal-footer" style="display:flex;gap:8px;justify-content:flex-end;padding-top:8px;border-top:1px solid var(--border);margin-top:6px;">
          <button class="sig-clear confirm-btn confirm-btn-secondary">Clear</button>
          <button class="sig-undo confirm-btn confirm-btn-secondary">Undo</button>
          <span style="flex:1;"></span>
          <button class="sig-cancel confirm-btn confirm-btn-secondary">Cancel</button>
          <button class="sig-save confirm-btn confirm-btn-primary" disabled>Save</button>
        </div>
      </div>
    `);

    const canvas = overlay.querySelector('.sig-canvas');
    const pad = new SmoothPad(canvas, _smoothnessToParams(initialSmooth));
    const slider = overlay.querySelector('.sig-smoothness');
    const sliderVal = overlay.querySelector('.sig-smoothness-val');
    slider.addEventListener('input', () => {
      const v = parseInt(slider.value, 10);
      sliderVal.textContent = String(v);
      _saveSmoothness(v);
      _applySmoothness(pad, v);
    });
    const saveBtn = overlay.querySelector('.sig-save');
    const nameInput = overlay.querySelector('.sig-name');

    const refreshSaveBtn = () => { saveBtn.disabled = pad.isEmpty(); };
    canvas.addEventListener('pointerup', refreshSaveBtn);
    canvas.addEventListener('pointerleave', refreshSaveBtn);
    canvas.addEventListener('pointercancel', refreshSaveBtn);

    const close = (val) => { overlay.remove(); resolve(val); };
    overlay.querySelector('.sig-close').onclick = () => close(null);
    overlay.querySelector('.sig-cancel').onclick = () => close(null);
    overlay.querySelector('.sig-clear').onclick = () => { pad.clear(); refreshSaveBtn(); };
    overlay.querySelector('.sig-undo').onclick = () => { pad.undo(); refreshSaveBtn(); };
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(null); });

    saveBtn.onclick = async () => {
      saveBtn.disabled = true;
      try {
        const trimmed = pad.toTrimmedDataUrl();
        const sig = await _saveSignature({
          dataUrl: trimmed.dataUrl,
          width: trimmed.width,
          height: trimmed.height,
          name: (nameInput.value || '').trim() || 'Signature',
        });
        const out = {
          id: sig.id,
          dataUrl: sig.data_url,
          width: sig.width,
          height: sig.height,
          name: sig.name,
        };
        setLastUsed(out);
        close(out);
      } catch (e) {
        alert('Failed to save signature: ' + e.message);
        saveBtn.disabled = false;
      }
    };
  });
}

// ── pick: show saved signatures + new tile ───────────────────────────────
export function pick(opts = {}) {
  return new Promise(async (resolve) => {
    const sigs = await _listSignatures();
    const tiles = sigs.map((s) => {
      const dataUrl = _safeSignatureDataUrl(s.data_url);
      if (!dataUrl) return '';
      return `
      <div class="sig-tile" data-id="${_esc(s.id)}">
        <img src="${_esc(dataUrl)}"/>
        <div style="margin-top:4px;font-size:0.72rem;color:var(--fg);opacity:0.85;text-align:center;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${_esc(s.name || '')}</div>
        <button class="sig-tile-del" data-id="${_esc(s.id)}" title="Delete">×</button>
      </div>
    `;
    }).join('');

    const overlay = _modal(`
      <div class="modal-content" style="width:min(560px,94vw);">
        <div class="modal-header">
          <h4>Choose a signature</h4>
          <button class="sig-close modal-close" title="Close">×</button>
        </div>
        <div class="modal-body">
          <button class="sig-new-tile confirm-btn confirm-btn-primary" style="width:100%;margin-bottom:12px;padding:8px;">+ Draw new signature</button>
          ${tiles ? `<div style="display:grid;grid-template-columns:repeat(3, 1fr);gap:10px;">${tiles}</div>` : '<div style="opacity:0.6;font-size:0.8rem;text-align:center;padding:8px 0;">No saved signatures yet — draw one above.</div>'}
        </div>
      </div>
    `);

    const close = (val) => { overlay.remove(); resolve(val); };
    overlay.querySelector('.sig-close').onclick = () => close(null);
    overlay.addEventListener('click', (e) => { if (e.target === overlay) close(null); });

    overlay.querySelectorAll('.sig-tile').forEach((tile) => {
      tile.addEventListener('click', (e) => {
        if (e.target.classList.contains('sig-tile-del')) return;
        const id = tile.dataset.id;
        const s = sigs.find((x) => x.id === id);
        if (s) {
          const dataUrl = _safeSignatureDataUrl(s.data_url);
          if (!dataUrl) return;
          const out = { id: s.id, dataUrl, width: s.width, height: s.height, name: s.name };
          setLastUsed(out);
          close(out);
        }
      });
    });
    overlay.querySelectorAll('.sig-tile-del').forEach((btn) => {
      btn.addEventListener('click', async (e) => {
        e.stopPropagation();
        const id = btn.dataset.id;
        if (!await window.styledConfirm('Delete this signature?', { confirmText: 'Delete', danger: true })) return;
        await _deleteSignature(id);
        btn.closest('.sig-tile')?.remove();
      });
    });
    overlay.querySelector('.sig-new-tile').onclick = async () => {
      overlay.remove();
      const created = await capture(opts);
      if (created) setLastUsed(created);
      resolve(created);
    };
  });
}

export default { capture, pick, getLastUsed, setLastUsed };
