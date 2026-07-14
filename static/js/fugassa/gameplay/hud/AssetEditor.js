import * as api from '../../fugassaApi.js';
import { escapeHtml } from '../screens/InventoryScreen.js';

const ASSET_POLL_MS = 2000;

/**
 * Image viewer + generate/regenerate — ADR §L12. Renders into `el` for the
 * active (non-archived) asset of `entityType`/`entityId`, if any. Primary
 * affordance is a small icon overlaid on the bottom-right corner of the
 * image itself (generate when there's no image yet, regenerate once there
 * is one); the prompt textareas are tucked behind a collapsible "Edit
 * prompt" toggle for players who want manual control.
 *
 * NPC portrait prompts live on `npcs.portrait_prompt` before any asset row
 * exists — `pending_prompt` from assets-meta (or `defaultPositivePrompt`)
 * pre-fills the editor so players can see/edit them.
 */
export async function mountAssetEditor(el, {
  saveId,
  entityType,
  entityId,
  assetType = entityType === 'npc' || entityType === 'player_character' ? 'portrait' : 'scene',
  title = 'Image',
  defaultPositivePrompt = '',
  defaultNegativePrompt = '',
  onPipelineActivity,
  onAssetReady,
} = {}) {
  el.className = `fugassa-asset-panel${assetType === 'scene' ? ' fugassa-asset-panel--scene' : ''}`;
  el.innerHTML = '<p class="fugassa-muted">Loading asset…</p>';
  if (!saveId || !entityId) {
    el.innerHTML = '<p class="fugassa-muted">No asset target.</p>';
    return null;
  }

  let asset = null;
  let pendingPrompt = String(defaultPositivePrompt || '').trim();
  let pendingNegative = String(defaultNegativePrompt || '').trim();
  let error = null;
  let pollTimer = null;

  const stopPoll = () => {
    if (pollTimer) {
      clearInterval(pollTimer);
      pollTimer = null;
    }
  };

  const refreshFromServer = async () => {
    const res = await api.getAssetsMeta(saveId, { entity_type: entityType, entity_id: entityId, asset_type: assetType });
    asset = (res.assets || []).find((a) => a.status !== 'archived') || null;
    if (res.pending_prompt) pendingPrompt = String(res.pending_prompt).trim();
    if (res.pending_negative_prompt) pendingNegative = String(res.pending_negative_prompt).trim();
    return asset?.status || null;
  };

  const ensurePoll = () => {
    if (!asset || (asset.status !== 'queued' && asset.status !== 'generating')) {
      stopPoll();
      return;
    }
    if (pollTimer) return;
    pollTimer = setInterval(async () => {
      try {
        const prevPath = asset?.file_path;
        await refreshFromServer();
        if (asset?.status === 'ready' || asset?.status === 'failed' || asset?.file_path !== prevPath) {
          error = null;
          render();
          if (asset?.status === 'ready' && asset?.file_path && asset.file_path !== prevPath) {
            onAssetReady?.({
              entityType,
              entityId,
              assetType,
              filePath: asset.file_path,
            });
          }
        }
        if (asset?.status !== 'queued' && asset?.status !== 'generating') {
          stopPoll();
        }
      } catch {
        // best-effort poll
      }
    }, ASSET_POLL_MS);
  };

  try {
    await refreshFromServer();
  } catch (err) {
    error = err;
  }

  const render = () => {
    const imgUrl = asset?.file_path
      ? `/api/fugassa/saves/${encodeURIComponent(saveId)}/assets/${encodeURIComponent(asset.file_path)}`
      : null;
    const isBusy = asset && (asset.status === 'queued' || asset.status === 'generating');
    const iconLabel = imgUrl ? '⟳' : '✨';
    const iconTitle = imgUrl ? 'Regenerate image' : 'Generate image';
    const posText = String(pendingPrompt || asset?.prompt || '').trim();
    const negText = String(pendingNegative || asset?.negative_prompt || '').trim();
    const hasPrompt = Boolean(posText);
    const showEditPanel = hasPrompt;

    el.innerHTML = `
      <h4>${escapeHtml(title)}</h4>
      <div class="fugassa-asset-image-wrap">
        ${imgUrl ? `<img src="${imgUrl}" alt="" />` : '<div class="fugassa-asset-placeholder">No image yet</div>'}
        ${isBusy ? '<div class="fugassa-asset-spinner-overlay"><span class="fugassa-spinner"></span></div>' : ''}
        <button type="button" class="fugassa-asset-regen-icon" data-asset-action title="${escapeHtml(iconTitle)}" ${isBusy ? 'disabled' : ''}>${iconLabel}</button>
        <button type="button" class="fugassa-asset-edit-icon" data-asset-toggle-edit title="Edit prompt">✎</button>
      </div>
      <div class="fugassa-asset-edit" data-asset-edit ${showEditPanel ? '' : 'hidden'}>
        <label class="fugassa-field"><span>Prompt</span><textarea data-pos rows="5">${escapeHtml(posText)}</textarea></label>
        <label class="fugassa-field"><span>Negative prompt</span><textarea data-neg rows="3">${escapeHtml(negText)}</textarea></label>
        <div class="fugassa-inline-actions">
          <button type="button" class="fugassa-btn fugassa-btn--sm" data-save-prompt>Save prompt</button>
          <button type="button" class="fugassa-btn fugassa-btn--sm fugassa-btn--primary" data-regen-manual>Generate with this prompt</button>
        </div>
      </div>
      <p class="fugassa-muted" data-status>${error ? escapeHtml(error.message || String(error)) : ''}</p>
    `;

    const status = el.querySelector('[data-status]');
    const posEl = el.querySelector('[data-pos]');
    const negEl = el.querySelector('[data-neg]');

    el.querySelector('[data-asset-toggle-edit]').addEventListener('click', () => {
      const box = el.querySelector('[data-asset-edit]');
      box.hidden = !box.hidden;
    });

    const runGenerate = async (useAuto, overridePositive, overrideNegative) => {
      status.textContent = imgUrl ? 'Regenerating…' : 'Generating…';
      el.querySelector('[data-asset-action]').disabled = true;
      try {
        const res = await api.generateAsset(saveId, {
          entityType,
          entityId,
          assetType,
          positivePrompt: overridePositive,
          negativePrompt: overrideNegative,
          useAutoPrompt: useAuto,
        });
        asset = res.asset || asset;
        await refreshFromServer();
        error = null;
        onPipelineActivity?.();
      } catch (err) {
        error = err;
      }
      render();
      ensurePoll();
    };

    el.querySelector('[data-asset-action]').addEventListener('click', () => {
      const manualPos = posEl?.value?.trim();
      if (manualPos) {
        runGenerate(false, manualPos, negEl?.value?.trim() || undefined);
      } else {
        runGenerate(true);
      }
    });
    el.querySelector('[data-save-prompt]')?.addEventListener('click', async () => {
      status.textContent = 'Saving…';
      try {
        if (asset?.id) {
          await api.patchAssetPrompt(saveId, asset.id, { positive_prompt: posEl.value, negative_prompt: negEl.value });
        } else if (entityType === 'npc') {
          await api.patchNpcPortraitPrompt(saveId, entityId, { positive_prompt: posEl.value, negative_prompt: negEl.value });
          pendingPrompt = posEl.value.trim();
          pendingNegative = negEl.value.trim();
        } else if (entityType === 'player_character') {
          await api.patchPlayerPortraitPrompt(saveId, { positive_prompt: posEl.value, negative_prompt: negEl.value });
          pendingPrompt = posEl.value.trim();
          pendingNegative = negEl.value.trim();
        } else {
          status.textContent = 'Generate an image first to save its prompt.';
          return;
        }
        status.textContent = 'Prompt saved.';
      } catch (err) {
        status.textContent = err.message || String(err);
      }
    });
    el.querySelector('[data-regen-manual]')?.addEventListener('click', () => runGenerate(false, posEl.value, negEl.value));

    ensurePoll();
  };

  render();

  return {
    refreshMeta: async () => {
      try {
        await refreshFromServer();
        error = null;
        render();
      } catch (err) {
        error = err;
        render();
      }
    },
    destroy: stopPoll,
  };
}
