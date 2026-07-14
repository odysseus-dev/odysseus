import * as api from '../../fugassaApi.js';

let activeCenter = null;

export function mountCenterView(el, { state, saveId, onPipelineActivity }) {
  if (activeCenter?.root === el) {
    activeCenter.update({ state, saveId, onPipelineActivity });
    return activeCenter;
  }
  activeCenter?.destroy?.();
  activeCenter = createCenterView(el, { state, saveId, onPipelineActivity });
  return activeCenter;
}

function createCenterView(el, { state, saveId, onPipelineActivity: initialOnPipeline }) {
  el.className = 'fugassa-hud-center';
  let currentState = state;
  let currentSaveId = saveId;
  let onPipelineActivity = initialOnPipeline;
  let locationId = state?.location_state?.location_id;
  let busy = false;
  let metaTimer = null;

  el.innerHTML = `
    <div class="fugassa-hud-panel-head">
      <h3 data-scene-title></h3>
      <p class="fugassa-muted fugassa-hud-scene-parent" data-scene-parent hidden></p>
    </div>
    <p class="fugassa-hud-center-desc fugassa-muted" data-scene-desc hidden></p>
    <div class="fugassa-hud-center-viewport" data-scene-wrap>
      <button type="button" class="fugassa-asset-regen-icon fugassa-hud-scene-regen" data-scene-regen title="Generate scene image" hidden>✨</button>
    </div>
  `;

  const titleEl = el.querySelector('[data-scene-title]');
  const parentEl = el.querySelector('[data-scene-parent]');
  const wrap = el.querySelector('[data-scene-wrap]');
  const descEl = el.querySelector('[data-scene-desc]');
  const regenBtn = wrap.querySelector('[data-scene-regen]');

  const setDescriptionVisible = (visible) => {
    descEl.hidden = !visible;
  };

  const renderHeader = () => {
    const loc = currentState?.location_state || {};
    const settlement = String(loc.settlement_name || '').trim();
    const district = String(loc.district_name || loc.name || 'Scene').trim();
    if (loc.is_sublocation && (loc.parent_area || loc.parent_name)) {
      titleEl.textContent = loc.name || district;
      parentEl.textContent = settlement
        ? `${settlement} · Sublocation · ${loc.parent_area || loc.parent_name}`
        : `Sublocation · ${loc.parent_area || loc.parent_name}`;
      parentEl.hidden = false;
    } else if (settlement && district && settlement.toLowerCase() !== district.toLowerCase()) {
      titleEl.textContent = district;
      parentEl.textContent = settlement;
      parentEl.hidden = false;
    } else {
      titleEl.textContent = loc.name || 'Scene';
      parentEl.textContent = '';
      parentEl.hidden = true;
    }
    descEl.textContent = loc.description || 'First-person view';
    const showRegen = Boolean(locationId && currentSaveId);
    regenBtn.hidden = !showRegen;
    regenBtn.disabled = busy || !showRegen;
  };

  const renderScene = (filePath) => {
    let img = wrap.querySelector('.fugassa-hud-scene');
    if (filePath) {
      const url = `/api/fugassa/saves/${encodeURIComponent(currentSaveId)}/assets/${encodeURIComponent(filePath)}`;
      if (!img) {
        img = document.createElement('img');
        img.className = 'fugassa-hud-scene';
        img.alt = '';
        wrap.insertBefore(img, regenBtn);
      }
      img.src = url;
      img.hidden = false;
      setDescriptionVisible(false);
      regenBtn.textContent = '⟳';
      regenBtn.title = 'Regenerate scene image';
      regenBtn.classList.add('fugassa-chat-scene-icon--ready');
    } else {
      img?.remove();
      setDescriptionVisible(true);
      regenBtn.textContent = '✨';
      regenBtn.title = 'Generate scene image';
      regenBtn.classList.remove('fugassa-chat-scene-icon--ready');
    }
  };

  const refreshMeta = async () => {
    if (!locationId || !currentSaveId) return;
    try {
      const res = await api.getAssetsMeta(currentSaveId, {
        entity_type: 'location',
        entity_id: locationId,
        asset_type: 'scene',
      });
      const asset = (res.assets || []).find((a) => a.status === 'ready' && a.file_path) || null;
      if (asset?.file_path) {
        renderScene(asset.file_path);
        regenBtn.disabled = busy;
        return;
      }
      const pending = (res.assets || []).find((a) => a.status === 'queued' || a.status === 'generating');
      const failed = (res.assets || []).find((a) => a.status === 'failed');
      if (pending) {
        regenBtn.textContent = '…';
        regenBtn.disabled = true;
        regenBtn.title = pending.status === 'generating' ? 'Generating scene…' : 'Queued for generation…';
      } else if (failed) {
        regenBtn.textContent = '⚠';
        regenBtn.disabled = busy;
        regenBtn.title = failed.error || 'Scene generation failed — click to retry';
      } else {
        renderScene(currentState?.location_state?.scene_asset || null);
        regenBtn.disabled = busy;
      }
    } catch {
      renderScene(currentState?.location_state?.scene_asset || null);
    }
  };

  const scheduleMetaRefresh = () => {
    if (metaTimer) return;
    metaTimer = setTimeout(async () => {
      metaTimer = null;
      await refreshMeta();
    }, 400);
  };

  regenBtn.addEventListener('click', async () => {
    if (busy || !locationId || !currentSaveId) return;
    busy = true;
    regenBtn.disabled = true;
    regenBtn.textContent = '…';
    try {
      const res = await api.generateAsset(currentSaveId, {
        entityType: 'location',
        entityId: locationId,
        assetType: 'scene',
        useAutoPrompt: true,
      });
      if (res.state?.location_state?.scene_asset) {
        renderScene(res.state.location_state.scene_asset);
      }
      onPipelineActivity?.();
      await refreshMeta();
    } catch (err) {
      regenBtn.textContent = '⚠';
      regenBtn.title = err?.message || 'Scene generation failed';
      await refreshMeta();
    } finally {
      busy = false;
      regenBtn.disabled = false;
    }
  });

  renderHeader();
  renderScene(currentState?.location_state?.scene_asset || null);
  refreshMeta();

  return {
    root: el,
    update({ state: nextState, saveId: nextSaveId, onPipelineActivity: nextOnPipeline }) {
      currentState = nextState;
      currentSaveId = nextSaveId;
      if (typeof nextOnPipeline === 'function') {
        onPipelineActivity = nextOnPipeline;
      }
      const nextLocId = nextState?.location_state?.location_id;
      const locChanged = nextLocId !== locationId;
      locationId = nextLocId;
      renderHeader();
      if (locChanged) {
        renderScene(null);
      }
      scheduleMetaRefresh();
    },
    refreshMeta,
    destroy() {
      if (metaTimer) clearTimeout(metaTimer);
      if (activeCenter?.root === el) activeCenter = null;
    },
  };
}
