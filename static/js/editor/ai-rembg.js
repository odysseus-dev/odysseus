/**
 * Background Remove (rembg) + Sharpen wiring + the live edge-cleanup
 * tuner that runs on the most-recent rembg cutout.
 *
 *   rembg-run button: flatten + POST to /api/image/remove-bg with an
 *     optional hint_mask if the user has a wand/lasso selection
 *     active. After the new layer lands, hides every previously-
 *     visible layer so the cutout reads cleanly, and binds the
 *     live-tuner to the new layer.
 *
 *   Live edge-cleanup tuner: snapshots the pristine cutout the
 *     moment it lands; subsequent feather/grow slider tweaks
 *     rebuild the layer's alpha from that snapshot WITHOUT
 *     re-running the model.
 *      - Grow > 0 → blur snap alpha, threshold low (32) → grow.
 *      - Grow < 0 → blur snap alpha, threshold high (200) → shrink.
 *      - Feather > 0 → blur the whole layer (alpha + RGB) so the
 *        edge softens AND the residual color fringe from the
 *        original background gets blurred away.
 *
 *   Sharpen: small slider + button; just calls _applyImageTool
 *     against /api/image/sharpen.
 *
 *   buildSelectionHintMask: pure-ish utility — returns a base64 PNG
 *     (no data: prefix) of the active wand or lasso selection, or
 *     null. Returned so other wand-rembg call sites can use it.
 *
 * @param {{
 *   applyImageTool:             (endpoint, payload, layerName, btn, opts?) => Promise<void>,
 *   openCookbookForDependency:  (pkg: string) => void,
 *   composite:                  () => void,
 *   renderLayerPanel:           () => void,
 *   uiModule:                   object,
 * }} deps
 *
 * @returns {{ buildSelectionHintMask: () => string | null }}
 */
import { state } from './state.js';

export function wireRembgAndSharpen({
  applyImageTool, openCookbookForDependency,
  composite, renderLayerPanel, uiModule,
}) {
  // ── Sharpen ──
  const sharpenPrev = document.getElementById('ge-sharpen-preview');
  if (sharpenPrev) sharpenPrev.style.opacity = '0.5';
  document.getElementById('ge-sharpen-amount')?.addEventListener('input', (e) => {
    document.getElementById('ge-sharpen-label').textContent = e.target.value + '%';
    if (sharpenPrev) sharpenPrev.style.opacity = (parseInt(e.target.value, 10) / 100).toFixed(2);
  });
  document.getElementById('ge-sharpen-run')?.addEventListener('click', () => {
    const amount = parseInt(document.getElementById('ge-sharpen-amount')?.value || '50');
    applyImageTool('/api/image/sharpen', { amount }, window.t('Sharpened'), document.getElementById('ge-sharpen-run'));
  });

  // ── Bg Remove ──
  document.getElementById('ge-rembg-install-link')?.addEventListener('click', () => {
    openCookbookForDependency('rembg');
  });
  document.getElementById('ge-rembg-run')?.addEventListener('click', async () => {
    const payload = {};
    const hint = buildSelectionHintMask();
    if (hint) payload.hint_mask = hint;
    // NB: edge_feather / edge_grow are applied CLIENT-side so the
    // sliders can re-tune the cutout without re-running the model.
    const btn = document.getElementById('ge-rembg-run');
    const before = state.layers.length;
    // Snapshot which layers were visible BEFORE the run so we know
    // which to hide after a successful cutout.
    const prevVisible = state.layers.filter(l => l.visible).map(l => l.id);
    await applyImageTool('/api/image/remove-bg', payload, window.t('BG Removed'), btn);
    // applyImageTool finishes after fetch but the new layer is added
    // inside img.onload (one tick later). Poll for up to 60 frames
    // (~1s) for the new layer to appear before we auto-hide.
    let frames = 0;
    while (state.layers.length <= before && frames < 60) {
      await new Promise(r => requestAnimationFrame(r));
      frames++;
    }
    if (state.layers.length > before) {
      const newLayer = state.layers[state.layers.length - 1];
      bindRembgLiveTuner(newLayer);
      // Auto-hide underlying layers so the user sees just the
      // cutout — the eye toggles back on if they re-enable manually.
      for (const layer of state.layers) {
        if (prevVisible.includes(layer.id) && layer.id !== newLayer.id) {
          layer.visible = false;
        }
      }
      composite();
      renderLayerPanel();
    }
    // Reset sliders so the new cutout starts clean.
    const f = document.getElementById('ge-rembg-feather');
    const g = document.getElementById('ge-rembg-grow');
    if (f) { f.value = 0; document.getElementById('ge-rembg-feather-label').textContent = '0px'; syncRembgFeather(0); }
    if (g) { g.value = 0; document.getElementById('ge-rembg-grow-label').textContent = '0px'; syncRembgGrow(0); }
  });

  // ── Live edge-cleanup tuner ──
  // Snapshots the pristine cutout the moment it lands; slider tweaks
  // rebuild alpha from that snapshot.
  function bindRembgLiveTuner(layer) {
    if (!layer) return;
    const w = layer.canvas.width, h = layer.canvas.height;
    const snap = document.createElement('canvas');
    snap.width = w; snap.height = h;
    snap.getContext('2d').drawImage(layer.canvas, 0, 0);
    state.rembgLiveLayer = layer;
    state.rembgLiveSnap = snap;
    rembgApplyEdgeNow();  // initial pass (no-op at 0/0)
  }
  let rembgRaf = null;
  function scheduleRembgApply() {
    if (rembgRaf) return;
    rembgRaf = requestAnimationFrame(() => { rembgRaf = null; rembgApplyEdgeNow(); });
  }
  function rembgApplyEdgeNow() {
    if (!state.rembgLiveLayer || !state.rembgLiveSnap) return;
    const feather = parseInt(document.getElementById('ge-rembg-feather')?.value || '0', 10);
    const grow = parseInt(document.getElementById('ge-rembg-grow')?.value || '0', 10);
    const layer = state.rembgLiveLayer;
    const snap = state.rembgLiveSnap;
    const w = snap.width, h = snap.height;
    const lctx = layer.ctx;

    // 1) Start fresh from the pristine cutout snapshot.
    lctx.clearRect(0, 0, w, h);
    lctx.drawImage(snap, 0, 0);

    // 2) Edge ±N — dilate / erode alpha via blur+threshold:
    //      grow > 0 → low threshold (32) → halo counts as opaque → grows.
    //      grow < 0 → high threshold (200) → only solid interior → shrinks.
    //    RGB is kept; only alpha is replaced.
    if (grow !== 0) {
      const blurC = document.createElement('canvas');
      blurC.width = w; blurC.height = h;
      const bctx = blurC.getContext('2d');
      bctx.filter = `blur(${Math.abs(grow)}px)`;
      bctx.drawImage(snap, 0, 0);
      bctx.filter = 'none';
      const blurred = bctx.getImageData(0, 0, w, h).data;
      const layerData = lctx.getImageData(0, 0, w, h);
      const out = layerData.data;
      const thr = grow > 0 ? 32 : 200;
      for (let i = 0; i < out.length; i += 4) {
        out[i + 3] = blurred[i + 3] >= thr ? 255 : 0;
      }
      lctx.putImageData(layerData, 0, 0);
    }

    // 3) Feather softens whatever edge we have now. Blur the entire
    //    layer (alpha + RGB) — alpha gets smooth falloff, RGB gets a
    //    faint blur at the edge which actually helps hide residual
    //    colour fringing from the original background.
    if (feather > 0) {
      const fC = document.createElement('canvas');
      fC.width = w; fC.height = h;
      const fctx = fC.getContext('2d');
      fctx.filter = `blur(${feather}px)`;
      fctx.drawImage(layer.canvas, 0, 0);
      fctx.filter = 'none';
      lctx.clearRect(0, 0, w, h);
      lctx.drawImage(fC, 0, 0);
    }
    composite();
  }

  // ── Slider preview swatches + wiring ──
  const rembgFeatherPrev = document.getElementById('ge-rembg-feather-preview');
  const rembgGrowPrev = document.getElementById('ge-rembg-grow-preview');
  function syncRembgFeather(v) {
    if (!rembgFeatherPrev) return;
    const inner = Math.max(0, 50 - v * 2.5);
    rembgFeatherPrev.style.background = `radial-gradient(circle, var(--fg) 0%, var(--fg) ${inner}%, transparent 75%)`;
  }
  function syncRembgGrow(v) {
    if (!rembgGrowPrev) return;
    // -10..+10 → scale 0.6 .. 1.4 so the swatch visibly grows/shrinks.
    const s = 1 + v * 0.04;
    rembgGrowPrev.style.transform = `scale(${s})`;
    rembgGrowPrev.style.background = v < 0 ? 'color-mix(in srgb, var(--fg) 40%, transparent)' : 'var(--fg)';
  }
  syncRembgFeather(2);
  syncRembgGrow(0);
  document.getElementById('ge-rembg-feather')?.addEventListener('input', (e) => {
    const v = parseInt(e.target.value, 10);
    document.getElementById('ge-rembg-feather-label').textContent = v + 'px';
    syncRembgFeather(v);
    scheduleRembgApply();
  });
  document.getElementById('ge-rembg-grow')?.addEventListener('input', (e) => {
    const v = parseInt(e.target.value, 10);
    document.getElementById('ge-rembg-grow-label').textContent = (v >= 0 ? '+' : '') + v + 'px';
    syncRembgGrow(v);
    scheduleRembgApply();
  });

  // ── Selection-hint mask builder (used here + by wand-rembg) ──
  // Full-image white-on-transparent mask PNG (base64, no `data:`
  // prefix) of whichever selection is active — wand first, lasso
  // second. Returns null if neither has a selection.
  function buildSelectionHintMask() {
    const w = state.imgWidth, h = state.imgHeight;
    if (state.wandMask && state.wandLayerId) {
      const off = state.layerOffsets.get(state.wandLayerId) || { x: 0, y: 0 };
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      c.getContext('2d').drawImage(state.wandMask, off.x, off.y);
      return c.toDataURL('image/png').split(',')[1];
    }
    if (state.lassoPoints.length >= 3 && !state.lassoActive) {
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      const ctx = c.getContext('2d');
      ctx.fillStyle = '#fff';
      ctx.beginPath();
      ctx.moveTo(state.lassoPoints[0].x, state.lassoPoints[0].y);
      for (let i = 1; i < state.lassoPoints.length; i++) {
        ctx.lineTo(state.lassoPoints[i].x, state.lassoPoints[i].y);
      }
      ctx.closePath();
      ctx.fill();
      return c.toDataURL('image/png').split(',')[1];
    }
    return null;
  }

  return { buildSelectionHintMask };
}
