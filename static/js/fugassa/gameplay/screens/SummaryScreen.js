import * as api from '../../fugassaApi.js';
import { escapeHtml } from './InventoryScreen.js';

const MAJOR_EVENT_TYPES = new Set([
  'quest_complete',
  'quest_failed',
  'companion_join',
  'companion_leave',
  'property_acquired',
  'title_granted',
  'combat_start',
  'combat_end',
  'npc_died',
  'level_up',
]);

function formatPartyLine(party) {
  if (!party?.length) return 'No party members';
  return party.map((m) => {
    const name = escapeHtml(m.name || 'Unknown');
    const role = m.role === 'hero' ? 'hero' : 'companion';
    return `<span class="fugassa-summary-party-chip">${name} <em class="fugassa-muted">(${role})</em></span>`;
  }).join('');
}

function renderCampaignState(cs) {
  if (!cs || typeof cs !== 'object') {
    return '<p class="fugassa-muted">No campaign state available.</p>';
  }
  const loc = cs.location || {};
  const place = [loc.settlement, loc.place].filter(Boolean).join(' · ') || loc.name || '—';
  const quests = cs.quests || {};
  const active = (quests.active || []).map((q) => escapeHtml(q.name || 'Quest')).join(', ') || 'none';
  const done = (quests.recently_completed || []).map((q) => escapeHtml(q.title || q.code || '')).join(', ') || 'none';
  const titles = cs.titles || {};
  const titleLine = titles.active_display
    ? escapeHtml(titles.active_display)
    : '<span class="fugassa-muted">none</span>';
  const prop = cs.property || {};
  const holdings = (prop.holdings || []).map((h) => {
    const name = escapeHtml(h.name || h.code || 'Property');
    const staff = (h.staff_names || []).length ? ` — staff: ${escapeHtml(h.staff_names.join(', '))}` : '';
    const active = prop.active_residence_code && h.code === prop.active_residence_code ? ' (active)' : '';
    return `<li>${name}${active}${staff}</li>`;
  }).join('') || '<li class="fugassa-muted">none</li>';

  return `
    <dl class="fugassa-summary-dl">
      <div><dt>Turn</dt><dd>${Number(cs.turn || 0)} · ${escapeHtml(cs.time_label || '')}</dd></div>
      <div><dt>Location</dt><dd>${escapeHtml(place)}</dd></div>
      <div><dt>Party</dt><dd class="fugassa-summary-party">${formatPartyLine(cs.party)}</dd></div>
      <div><dt>Active quests</dt><dd>${active}</dd></div>
      <div><dt>Recently completed</dt><dd>${done}</dd></div>
      <div><dt>Title</dt><dd>${titleLine}</dd></div>
      <div><dt>Property</dt><dd><ul class="fugassa-summary-inline-list">${holdings}</ul></dd></div>
      <div><dt>In combat</dt><dd>${cs.in_combat ? 'yes' : 'no'}</dd></div>
    </dl>
  `;
}

function formatChronicleWhen(createdAt) {
  const raw = String(createdAt || '').trim();
  if (!raw) return '';
  const date = new Date(raw);
  if (Number.isNaN(date.getTime())) return raw;
  return date.toLocaleString(undefined, { dateStyle: 'medium', timeStyle: 'short' });
}

function renderChronicle(events, majorOnly) {
  const rows = (events || []).filter((ev) => !majorOnly || MAJOR_EVENT_TYPES.has(ev.event_type));
  if (!rows.length) {
    return '<p class="fugassa-muted">No chronicle events yet.</p>';
  }
  return `<ul class="fugassa-summary-chronicle">${rows.map((ev) => {
    const when = formatChronicleWhen(ev.created_at);
    const whenLine = when ? `<span class="fugassa-muted fugassa-summary-chronicle-when">${escapeHtml(when)}</span>` : '';
    return `
    <li>
      <span class="fugassa-summary-chronicle-type">${escapeHtml(ev.event_type || 'event')}</span>
      <strong>${escapeHtml(ev.title || '')}</strong>
      <span class="fugassa-muted"> — turn ${Number(ev.turn_id || 0)}</span>
      ${whenLine}
      <p>${escapeHtml(ev.summary || '')}</p>
    </li>
  `;
  }).join('')}</ul>`;
}

function renderPinnedFacts(facts) {
  if (!facts?.length) return '<p class="fugassa-muted">No pinned facts yet.</p>';
  return `<ul class="fugassa-summary-list">${facts.map((f) => `<li>${escapeHtml(typeof f === 'string' ? f : f.text || String(f))}</li>`).join('')}</ul>`;
}

/**
 * Read-only campaign overview — ADR §7.1 five sections.
 */
export async function mountSummaryScreen(root, { saveId, onClose }) {
  root.className = 'fugassa-screen fugassa-screen--summary';
  root.innerHTML = `
    <header class="fugassa-screen-head">
      <h2>Campaign summary</h2>
      <button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-close>Back to game</button>
    </header>
    <div class="fugassa-screen-body fugassa-summary-layout" data-body>
      <p class="fugassa-muted">Loading…</p>
    </div>
  `;
  root.querySelector('[data-close]').addEventListener('click', () => onClose?.());
  const body = root.querySelector('[data-body]');

  let data = null;
  try {
    data = await api.getGameSummary(saveId);
  } catch (error) {
    body.innerHTML = `<p class="fugassa-muted">${escapeHtml(error.message || String(error))}</p>`;
    return;
  }

  let majorOnly = false;

  const render = () => {
    const digestText = String(data.digest_text || '').trim();
    const summaries = data.scene_summaries || [];
    body.innerHTML = `
      <section class="fugassa-screen-card fugassa-summary-section">
        <h3>Campaign state</h3>
        <p class="fugassa-muted">Canonical snapshot — party, quests, titles, property.</p>
        ${renderCampaignState(data.campaign_state)}
      </section>
      <section class="fugassa-screen-card fugassa-summary-section">
        <div class="fugassa-summary-section-head">
          <h3>Recent chronicle</h3>
          <label class="fugassa-summary-toggle">
            <input type="checkbox" data-major-only ${majorOnly ? 'checked' : ''} />
            Major events only
          </label>
        </div>
        ${renderChronicle(data.chronicle, majorOnly)}
      </section>
      <section class="fugassa-screen-card fugassa-summary-section">
        <h3>Pinned facts</h3>
        <p class="fugassa-muted">Engine-major events the GM must not contradict.</p>
        ${renderPinnedFacts(data.pinned_facts)}
      </section>
      <section class="fugassa-screen-card fugassa-summary-section">
        <h3>Campaign digest</h3>
        <p class="fugassa-muted">Condensed narrative of older turns (last condensed at turn ${data.last_condensed_turn ?? 0}).</p>
        ${digestText
          ? `<p class="fugassa-summary-digest">${escapeHtml(digestText).replace(/\n/g, '<br />')}</p>`
          : '<p class="fugassa-muted">No older turns condensed yet.</p>'}
      </section>
      <section class="fugassa-screen-card fugassa-summary-section">
        <h3>Scene summaries</h3>
        <p class="fugassa-muted">Recaps written when you leave a location.</p>
        ${summaries.length
          ? `<ul class="fugassa-summary-list">${summaries.map((s) => `
              <li>
                <strong>${escapeHtml(s.location_name || 'Unknown place')}</strong>
                <span class="fugassa-muted"> — turns ${s.turn_start ?? '?'}–${s.turn_end ?? '?'}</span>
                <p>${escapeHtml(s.summary_text || '')}</p>
              </li>
            `).join('')}</ul>`
          : '<p class="fugassa-muted">No scene summaries yet.</p>'}
      </section>
    `;
    body.querySelector('[data-major-only]')?.addEventListener('change', (ev) => {
      majorOnly = Boolean(ev.target.checked);
      render();
    });
  };

  render();
}
