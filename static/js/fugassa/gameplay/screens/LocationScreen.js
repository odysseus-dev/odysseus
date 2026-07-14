import { escapeHtml } from './InventoryScreen.js';
import { mountAssetEditor } from '../hud/AssetEditor.js';

export function mountLocationScreen(root, { state, saveId, onClose }) {
  root.className = 'fugassa-screen fugassa-screen--location';
  const loc = state?.location_state || {};

  const list = (items) => (items?.length
    ? items.map((x) => `<li>${escapeHtml(typeof x === 'object' ? x.name : x)}</li>`).join('')
    : '<li class="fugassa-muted">None</li>');

  root.innerHTML = `
    <header class="fugassa-screen-head">
      <h2>Location</h2>
      <button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-close>Back to game</button>
    </header>
    <div class="fugassa-screen-body fugassa-loc-layout">
      <section class="fugassa-screen-card">
        <h3>${escapeHtml(loc.name || 'Unknown')}</h3>
        <p>${escapeHtml(loc.description || '')}</p>
      </section>
      <section class="fugassa-screen-card">
        <h4>NPCs</h4>
        <ul class="fugassa-hud-list">${list(loc.npcs)}</ul>
        <h4>Enemies</h4>
        <ul class="fugassa-hud-list">${list(loc.enemies)}</ul>
        <h4>Loot</h4>
        <ul class="fugassa-hud-list">${list(loc.loot)}</ul>
        <h4>Sublocations</h4>
        <ul class="fugassa-hud-list">${list(loc.sublocations)}</ul>
      </section>
      <section class="fugassa-screen-card" data-asset-wrap></section>
    </div>
  `;
  root.querySelector('[data-close]').addEventListener('click', () => onClose?.());

  if (loc.location_id) {
    mountAssetEditor(root.querySelector('[data-asset-wrap]'), {
      saveId,
      entityType: 'location',
      entityId: loc.location_id,
      title: 'Scene image',
    });
  }
}
