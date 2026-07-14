import * as api from '../../fugassaApi.js';
import uiModule from '../../../ui.js';
import { escapeHtml } from './InventoryScreen.js';
import { mountAssetEditor } from '../hud/AssetEditor.js';

function kindLabel(kind) {
  return String(kind || 'residence').replace(/_/g, ' ');
}

function specsHtml(specs) {
  if (!specs || typeof specs !== 'object') return '';
  const rows = Object.entries(specs)
    .filter(([, v]) => v !== null && v !== undefined && v !== '')
    .map(([k, v]) => `<li><strong>${escapeHtml(k)}:</strong> ${escapeHtml(String(v))}</li>`)
    .join('');
  return rows ? `<ul class="fugassa-hud-list">${rows}</ul>` : '';
}

export async function mountEstatesScreen(root, { state, saveId, onClose, onStateChange }) {
  root.className = 'fugassa-screen fugassa-screen--estates';
  root.innerHTML = `
    <header class="fugassa-screen-head">
      <h2>Estates</h2>
      <button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-close>Back to game</button>
    </header>
    <div class="fugassa-screen-body fugassa-estates-layout" data-estates-body>
      <p class="fugassa-muted">Loading holdings…</p>
    </div>
  `;
  root.querySelector('[data-close]').addEventListener('click', () => onClose?.());

  const body = root.querySelector('[data-estates-body]');
  let payload = { holdings: [], active_residence_code: null };
  try {
    payload = await api.getGameProperties(saveId);
  } catch (error) {
    body.innerHTML = `<p class="fugassa-muted">${escapeHtml(error.message || String(error))}</p>`;
    return;
  }

  const holdings = payload.holdings || [];
  if (!holdings.length) {
    body.innerHTML = `
      <section class="fugassa-screen-card">
        <h3>No property holdings</h3>
        <p class="fugassa-muted">Your character does not own any registered residences yet. Property may be granted through the story or at campaign start for wealthy backgrounds.</p>
      </section>
    `;
    return;
  }

  let selectedCode = payload.active_residence_code || holdings[0]?.code || null;

  const render = () => {
    const selected = holdings.find((h) => h.code === selectedCode) || holdings[0];
    const cards = holdings.map((h) => {
      const active = h.code === payload.active_residence_code;
      return `
        <button type="button" class="fugassa-estate-card ${h.code === selected?.code ? 'is-selected' : ''}" data-estate-code="${escapeHtml(h.code)}">
          <span class="fugassa-estate-card__title">${escapeHtml(h.name || h.code)}</span>
          <span class="fugassa-muted">${escapeHtml(kindLabel(h.property_kind))} · ${escapeHtml(h.title_status || 'owned')}</span>
          ${active ? '<span class="fugassa-badge">Active residence</span>' : ''}
        </button>
      `;
    }).join('');

    const rooms = (selected?.rooms || [])
      .map(
        (r) => `
        <li>
          <button type="button" class="fugassa-estate-room-btn" data-room-id="${escapeHtml(String(r.id))}" title="Visit this room">
            ${escapeHtml(r.name)}
          </button>
          ${r.description ? `<span class="fugassa-muted fugassa-estate-room-desc">${escapeHtml(r.description)}</span>` : ''}
        </li>`,
      )
      .join('') || '<li class="fugassa-muted">No rooms registered yet</li>';

    const fixtures = (selected?.fixtures || [])
      .map(
        (f) => `
        <li>
          <strong>${escapeHtml(f.name)}</strong>
          <span class="fugassa-muted"> · ${escapeHtml(f.fixture_kind || 'fixture')}</span>
          ${f.room_name ? `<span class="fugassa-muted"> · ${escapeHtml(f.room_name)}</span>` : ''}
          <span class="fugassa-muted"> · ${escapeHtml(String(f.condition_pct ?? 100))}%</span>
          ${f.description ? `<div class="fugassa-muted">${escapeHtml(f.description)}</div>` : ''}
        </li>`,
      )
      .join('') || '<li class="fugassa-muted">No fixtures registered yet</li>';

    const staff = (selected?.staff || [])
      .map(
        (s) => `
        <li>
          <strong>${escapeHtml(s.name)}</strong>
          <span class="fugassa-muted"> · ${escapeHtml(s.role || 'staff')}</span>
        </li>`,
      )
      .join('') || '<li class="fugassa-muted">No staff assigned yet</li>';

    const staffNames = (selected?.staff_names || []).join(', ');

    body.innerHTML = `
      <div class="fugassa-estates-grid">
        <section class="fugassa-screen-card fugassa-estates-list">
          <h3>Portfolio</h3>
          <div class="fugassa-estate-cards">${cards}</div>
        </section>
        <section class="fugassa-screen-card fugassa-estates-detail">
          <h3>${escapeHtml(selected?.name || 'Holding')}</h3>
          <p class="fugassa-muted">${escapeHtml(kindLabel(selected?.property_kind))} · ${escapeHtml(selected?.title_status || 'owned')}</p>
          <p>${escapeHtml(selected?.deed_summary || '')}</p>
          ${specsHtml(selected?.specs)}
          ${staffNames ? `<p class="fugassa-muted">Staff: ${escapeHtml(staffNames)}</p>` : ''}
          <h4>Rooms</h4>
          <ul class="fugassa-hud-list fugassa-estate-rooms">${rooms}</ul>
          <h4>Fixtures</h4>
          <ul class="fugassa-hud-list fugassa-estate-fixtures">${fixtures}</ul>
          <h4>Staff</h4>
          <ul class="fugassa-hud-list fugassa-estate-staff">${staff}</ul>
          <div class="fugassa-inline-actions fugassa-estates-actions">
            <button type="button" class="fugassa-btn fugassa-btn--sm" data-visit>Visit</button>
            <button type="button" class="fugassa-btn fugassa-btn--sm fugassa-btn--ghost" data-set-active>Set as active residence</button>
          </div>
          <div class="fugassa-muted" data-estates-feedback hidden></div>
          <div data-asset-wrap></div>
        </section>
      </div>
    `;

    body.querySelectorAll('[data-estate-code]').forEach((btn) => {
      btn.addEventListener('click', () => {
        selectedCode = btn.dataset.estateCode;
        render();
      });
    });

    const feedback = body.querySelector('[data-estates-feedback]');
    const showFeedback = (text, ok = true) => {
      feedback.hidden = false;
      feedback.textContent = text;
      feedback.className = ok ? 'fugassa-muted' : 'fugassa-error-text';
    };

    body.querySelector('[data-visit]').addEventListener('click', async () => {
      if (!selected?.code) return;
      showFeedback('Traveling…');
      try {
        const res = await api.visitGameProperty(saveId, selected.code);
        if (res.state) onStateChange?.(res.state);
        showFeedback(res.message || 'You arrive at the property.');
        uiModule.showToast?.(res.message || 'Visited property', { duration: 2500, leadingIcon: 'check' });
        onClose?.();
      } catch (error) {
        showFeedback(error.message || String(error), false);
      }
    });

    body.querySelector('[data-set-active]').addEventListener('click', async () => {
      if (!selected?.code) return;
      showFeedback('Saving…');
      try {
        const res = await api.setActiveResidence(saveId, selected.code);
        payload.active_residence_code = selected.code;
        if (res.state) onStateChange?.(res.state);
        showFeedback('Active residence updated.');
        uiModule.showToast?.('Active residence updated', { duration: 2200, leadingIcon: 'check' });
        render();
      } catch (error) {
        showFeedback(error.message || String(error), false);
      }
    });

    body.querySelectorAll('[data-room-id]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        if (!selected?.code) return;
        const roomId = Number(btn.dataset.roomId);
        if (!roomId) return;
        showFeedback('Entering room…');
        try {
          const res = await api.visitGamePropertyRoom(saveId, selected.code, roomId);
          if (res.state) onStateChange?.(res.state);
          showFeedback(res.message || 'You enter the room.');
          uiModule.showToast?.(res.message || 'Entered room', { duration: 2500, leadingIcon: 'check' });
          onClose?.();
        } catch (error) {
          showFeedback(error.message || String(error), false);
        }
      });
    });

    if (selected?.root_location_id) {
      mountAssetEditor(body.querySelector('[data-asset-wrap]'), {
        saveId,
        entityType: 'location',
        entityId: selected.root_location_id,
        title: 'Property scene',
      });
    }
  };

  render();
}
