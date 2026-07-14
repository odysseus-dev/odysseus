import * as api from '../../fugassaApi.js';
import { mountAssetEditor } from './AssetEditor.js';
import { formatWalletText, walletFromState } from '../inventoryDisplay.js';

const MODES = [
  { id: 'explore', label: 'Explore' },
  { id: 'combat', label: 'Combat' },
  { id: 'npc', label: 'NPC' },
  { id: 'quests', label: 'Quests' },
];

let activeRightSidebar = null;
let expandedNpcId = null;
let portraitEditorCtrl = null;

export function mountRightSidebar(el, opts) {
  if (activeRightSidebar?.root === el) {
    activeRightSidebar.update(opts);
    return activeRightSidebar;
  }
  activeRightSidebar?.destroy?.();
  activeRightSidebar = createRightSidebar(el, opts);
  return activeRightSidebar;
}

function createRightSidebar(el, {
  state,
  mode,
  saveId,
  onModeChange,
  actions = {},
  onPipelineActivity,
  questsTabHasUpdate = false,
}) {
  el.className = 'fugassa-hud-right';
  let currentState = state;
  let currentMode = mode;
  let currentSaveId = saveId;
  let currentActions = actions;
  let currentOnPipelineActivity = onPipelineActivity;
  let currentQuestsTabHasUpdate = questsTabHasUpdate;
  let lastEffectiveMode = null;

  el.innerHTML = `
    <div class="fugassa-hud-panel-head fugassa-hud-right-tabs"></div>
    <div class="fugassa-hud-right-body"></div>
    <div class="fugassa-popup-backdrop" data-investigate-backdrop hidden>
      <div class="fugassa-popup" data-investigate-popup></div>
    </div>
    <div class="fugassa-popup-backdrop" data-loot-backdrop hidden>
      <div class="fugassa-popup" data-loot-popup></div>
    </div>
  `;
  const tabs = el.querySelector('.fugassa-hud-right-tabs');
  const body = el.querySelector('.fugassa-hud-right-body');
  const investigateBackdrop = el.querySelector('[data-investigate-backdrop]');
  const lootBackdrop = el.querySelector('[data-loot-backdrop]');
  [investigateBackdrop, lootBackdrop].forEach((backdrop) => {
    backdrop.addEventListener('click', (ev) => {
      if (ev.target === backdrop) backdrop.hidden = true;
    });
  });

  const getEffectiveMode = () => {
    const inCombat = Boolean(currentState?.in_combat);
    return inCombat && currentMode === 'explore' ? 'combat' : currentMode;
  };

  const renderTabs = () => {
    const effectiveMode = getEffectiveMode();
    tabs.innerHTML = '';
    MODES.forEach((m) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `fugassa-btn fugassa-btn--sm ${m.id === effectiveMode ? 'fugassa-hud-tab--active' : ''}`;
      if (m.id === 'quests' && currentQuestsTabHasUpdate) {
        btn.innerHTML = `${escapeHtml(m.label)} <span class="fugassa-hud-tab-badge">updated</span>`;
      } else {
        btn.textContent = m.label;
      }
      btn.addEventListener('click', () => onModeChange?.(m.id));
      tabs.appendChild(btn);
    });
  };

  const clearPortraitEditor = () => {
    portraitEditorCtrl?.destroy?.();
    portraitEditorCtrl = null;
  };

  const renderBody = (force = false) => {
    const wt = currentState?.world_time || {};
    const loc = currentState?.location_state || {};
    const inCombat = Boolean(currentState?.in_combat);
    const effectiveMode = getEffectiveMode();
    const timeHtml = timeDisplayHtml(wt);
    const walletText = formatWalletText(walletFromState(currentState));

    if (!force && effectiveMode === lastEffectiveMode && effectiveMode === 'explore') {
      updateExplorePanel(body.firstElementChild, currentState, loc, timeHtml, walletText);
      return;
    }

    if (!force && effectiveMode === lastEffectiveMode && effectiveMode === 'npc') {
      portraitEditorCtrl?.refreshMeta?.();
      syncNpcPortraitThumbs();
      return;
    }

    lastEffectiveMode = effectiveMode;
    clearPortraitEditor();
    body.innerHTML = '';
    if (effectiveMode === 'combat' || inCombat) {
      body.appendChild(renderCombatPanel(currentState, currentActions));
    } else if (effectiveMode === 'npc') {
      body.appendChild(renderNpcPanel(loc, currentActions, currentSaveId, {
        onPipelineActivity: currentOnPipelineActivity,
        expandedNpcId,
        onExpandChange: (npcId) => { expandedNpcId = npcId; },
        setPortraitEditor: (ctrl) => { portraitEditorCtrl = ctrl; },
        onNpcPortraitReady: handleNpcPortraitReady,
      }));
    } else if (effectiveMode === 'quests') {
      body.appendChild(renderQuestPanel(currentState));
    } else {
      body.appendChild(
        renderExplorePanel(currentState, loc, timeHtml, walletText, currentActions, { investigateBackdrop, lootBackdrop }),
      );
    }
  };

  const syncNpcPortraitThumbs = () => {
    const details = currentState?.location_state?.npc_details || [];
    details.forEach((npc) => {
      if (!npc?.npc_id) return;
      const card = body.querySelector(`[data-npc-id="${npc.npc_id}"]`);
      updateNpcCardPortrait(card, currentSaveId, npc.portrait_path);
    });
  };

  const handleNpcPortraitReady = ({ entityId, filePath }) => {
    if (!entityId || !filePath) return;
    const loc = currentState?.location_state;
    if (loc?.npc_details) {
      const row = loc.npc_details.find((d) => Number(d.npc_id) === Number(entityId));
      if (row) row.portrait_path = filePath;
    }
    const card = body.querySelector(`[data-npc-id="${entityId}"]`);
    updateNpcCardPortrait(card, currentSaveId, filePath);
  };

  renderTabs();
  renderBody(true);

  return {
    root: el,
    update(next) {
      currentState = next.state ?? currentState;
      currentMode = next.mode ?? currentMode;
      currentSaveId = next.saveId ?? currentSaveId;
      currentActions = next.actions ?? currentActions;
      if (typeof next.questsTabHasUpdate === 'boolean') {
        currentQuestsTabHasUpdate = next.questsTabHasUpdate;
      }
      if (typeof next.onPipelineActivity === 'function') {
        currentOnPipelineActivity = next.onPipelineActivity;
      }
      renderTabs();
      renderBody(false);
    },
    refreshNpcAssets() {
      portraitEditorCtrl?.refreshMeta?.();
      syncNpcPortraitThumbs();
    },
    destroy() {
      clearPortraitEditor();
      if (activeRightSidebar?.root === el) activeRightSidebar = null;
    },
  };
}

// world_time keys are populated from the GM's per-turn timestamp table (see
// gm_response_parser._extract_timestamp / game_session._apply_world_time) —
// era/year/month/moon_phase/season/weather are all optional enrichment on
// top of the always-present day/hour, so each line only renders when the
// GM has actually established that piece of context.
function timeDisplayHtml(wt) {
  const clock = formatWorldClock(wt);
  const lines = [];
  if (clock) lines.push(`<p>${escapeHtml(clock)}</p>`);
  const dateLine = formatWorldDate(wt);
  if (dateLine) lines.push(`<p class="fugassa-muted">${escapeHtml(dateLine)}</p>`);
  if (!lines.length) lines.push('<p class="fugassa-muted">Time unknown</p>');
  return lines.join('');
}

function looksLikeClock(text) {
  return /^\d{1,2}:\d{2}(\s*(AM|PM))?$/i.test(String(text || '').trim())
    || /\d{1,2}:\d{2}\s*(AM|PM)/i.test(String(text || ''));
}

function formatWorldClock(wt) {
  wt = wt || {};
  const parts = [];
  const tod = String(wt.time_of_day || '').trim();
  if (tod && !looksLikeClock(tod)) parts.push(tod);
  let clock = String(wt.hhmm || '').trim();
  if (!clock && wt.hour !== undefined && wt.hour !== null) {
    const h = Number(wt.hour) % 24;
    const m = Number(wt.minute || 0) % 60;
    const meridiem = h < 12 ? 'AM' : 'PM';
    const display = (h % 12) || 12;
    clock = `${display}:${String(m).padStart(2, '0')} ${meridiem}`;
  }
  if (clock && !parts.includes(clock)) parts.push(clock);
  if (!parts.length) return `Day ${wt.day ?? 1}`;
  return parts.join(' · ');
}

function formatWorldDate(wt) {
  wt = wt || {};
  const bits = [];
  if (wt.era) bits.push(String(wt.era));
  if (wt.year) bits.push(String(wt.year));
  if (wt.month) bits.push(String(wt.month));
  if (wt.day !== undefined && wt.day !== null) bits.push(String(wt.day));
  if (wt.season && !bits.includes(String(wt.season))) bits.push(String(wt.season));
  if (wt.moon_phase) bits.push(`Moon: ${wt.moon_phase}`);
  if (wt.weather) bits.push(String(wt.weather));
  return bits.join(' · ');
}

function playerCoordsText(state) {
  const player = state?.player || {};
  const anchor = player.sublocation_anchor;
  const x = anchor ? Number(anchor.x) : Number(player.x);
  const y = anchor ? Number(anchor.y) : Number(player.y);
  const z = anchor ? Number(anchor.z) : Number(player.z);
  const coords = `(${Number.isFinite(x) ? x : 0}, ${Number.isFinite(y) ? y : 0}, ${Number.isFinite(z) ? z : 0})`;
  if (player.sublocation_id) {
    const area = state?.location_state?.parent_area;
    return area ? `${coords} · sublocation` : `${coords} · interior`;
  }
  return coords;
}

function locationLabelHtml(loc) {
  const name = loc?.name || 'Unknown';
  const settlement = String(loc?.settlement_name || '').trim();
  const district = String(loc?.district_name || name).trim();
  if (loc?.is_sublocation && loc?.parent_area) {
    const city = settlement ? `<p class="fugassa-muted fugassa-hud-location-settlement">${escapeHtml(settlement)}</p>` : '';
    return `
      <p><strong>${escapeHtml(name)}</strong></p>
      <p class="fugassa-muted fugassa-hud-location-parent">Sublocation · ${escapeHtml(loc.parent_area)}</p>
      ${city}
    `;
  }
  if (loc?.is_sublocation && loc?.parent_name) {
    const city = settlement ? `<p class="fugassa-muted fugassa-hud-location-settlement">${escapeHtml(settlement)}</p>` : '';
    return `
      <p><strong>${escapeHtml(name)}</strong></p>
      <p class="fugassa-muted fugassa-hud-location-parent">Sublocation · ${escapeHtml(loc.parent_name)}</p>
      ${city}
    `;
  }
  if (settlement && district && settlement.toLowerCase() !== district.toLowerCase()) {
    return `
      <p><strong>${escapeHtml(district)}</strong></p>
      <p class="fugassa-muted fugassa-hud-location-settlement">${escapeHtml(settlement)}</p>
    `;
  }
  return `<p><strong>${escapeHtml(name)}</strong></p>`;
}

function updateExplorePanel(wrap, state, loc, timeHtml, walletText) {
  if (!wrap) return;
  const timeSec = wrap.querySelector('[data-hud-time]');
  if (timeSec) timeSec.innerHTML = timeHtml;
  const walletSec = wrap.querySelector('[data-hud-wallet]');
  if (walletSec) {
    walletSec.hidden = false;
    walletSec.innerHTML = walletText
      ? `<p>${escapeHtml(walletText)}</p>`
      : '<p class="fugassa-muted">No currency tiers configured.</p>';
  }
  const mini = wrap.querySelector('.fugassa-minimap');
  if (mini) {
    const minimapRows = state?.minimap || [];
    mini.innerHTML = '';
    mini.style.setProperty('--fugassa-minimap-cols', String(minimapRows[0]?.length || 5));
    minimapRows.forEach((row) => {
      row.forEach((cell) => {
        const t = document.createElement('span');
        t.className = `fugassa-minimap-cell fugassa-minimap-cell--${cell.state || 'fog'}`;
        t.title = cell.tooltip || '';
        t.setAttribute('aria-label', cell.tooltip || '');
        mini.appendChild(t);
      });
    });
  }
  const locSec = wrap.querySelector('[data-hud-location]');
  if (locSec) {
    locSec.innerHTML = `
      ${locationLabelHtml(loc)}
      <p class="fugassa-muted fugassa-hud-location-coords">${escapeHtml(playerCoordsText(state))}</p>
    `;
  }
}

function renderPartyExploreSection(state, actions) {
  const sec = document.createElement('div');
  sec.className = 'fugassa-hud-party-section';
  const companions = (state?.party || []).slice(1);
  if (!companions.length) {
    sec.innerHTML = '<p class="fugassa-muted">No companions yet.</p>';
    return sec;
  }
  companions.forEach((member) => {
    const row = document.createElement('div');
    row.className = 'fugassa-hud-party-row-item';
    const name = member.name || 'Companion';
    row.innerHTML = `<strong>${escapeHtml(name)}</strong>`;
    const actionsRow = document.createElement('div');
    actionsRow.className = 'fugassa-inline-actions';
    const talkBtn = btn('Talk', () => actions.companionTalk?.(member));
    const viewBtn = btn('View', () => actions.openCompanion?.(member));
    actionsRow.append(talkBtn, viewBtn);
    row.appendChild(actionsRow);
    sec.appendChild(row);
  });
  return sec;
}

function renderExplorePanel(state, loc, timeHtml, walletText, actions, popups, saveId) {
  const wrap = document.createElement('div');
  wrap.className = 'fugassa-hud-side-stack';

  wrap.appendChild(section('Time', timeHtml, { attrs: { 'data-hud-time': '' } }));
  wrap.appendChild(
    section('Currency', walletText ? `<p>${escapeHtml(walletText)}</p>` : '<p class="fugassa-muted">No holdings</p>', {
      attrs: { 'data-hud-wallet': '' },
    }),
  );

  const activeTitle = state?.player_titles?.active_display;
  wrap.appendChild(
    section(
      'Title',
      activeTitle
        ? `<p>${escapeHtml(activeTitle)}</p>`
        : '<p class="fugassa-muted">None yet — earned through quests.</p>',
    ),
  );

  const partySec = section('Party', '');
  partySec.appendChild(renderPartyExploreSection(state, actions));
  wrap.appendChild(partySec);

  const minimapRows = state?.minimap || [];
  const miniWrap = document.createElement('div');
  miniWrap.className = 'fugassa-minimap-wrap';
  const mini = document.createElement('div');
  mini.className = 'fugassa-minimap';
  mini.style.setProperty('--fugassa-minimap-cols', String(minimapRows[0]?.length || 5));
  minimapRows.forEach((row) => {
    row.forEach((cell) => {
      const t = document.createElement('span');
      t.className = `fugassa-minimap-cell fugassa-minimap-cell--${cell.state || 'fog'}`;
      t.title = cell.tooltip || '';
      t.setAttribute('aria-label', cell.tooltip || '');
      mini.appendChild(t);
    });
  });
  miniWrap.appendChild(mini);
  const legend = document.createElement('div');
  legend.className = 'fugassa-minimap-legend';
  legend.innerHTML = `
    <div class="fugassa-minimap-legend-row"><span class="fugassa-minimap-legend-swatch fugassa-minimap-legend-swatch--current"></span><span>You</span></div>
    <div class="fugassa-minimap-legend-row"><span class="fugassa-minimap-legend-swatch fugassa-minimap-legend-swatch--visited"></span><span>Visited</span></div>
    <div class="fugassa-minimap-legend-row"><span class="fugassa-minimap-legend-swatch fugassa-minimap-legend-swatch--intel"></span><span>Intel</span></div>
    <div class="fugassa-minimap-legend-row"><span class="fugassa-minimap-legend-swatch fugassa-minimap-legend-swatch--fog"></span><span>Unexplored</span></div>
  `;
  miniWrap.appendChild(legend);
  const mapSec = section('Map', '');
  mapSec.appendChild(miniWrap);
  const mapBtn = btn('Open Map', () => actions.openMap?.());
  mapSec.appendChild(mapBtn);
  wrap.appendChild(mapSec);

  const moveGrid = document.createElement('div');
  moveGrid.className = 'fugassa-move-grid';
  [
    ['', 'N', ''],
    ['W', '·', 'E'],
    ['', 'S', ''],
  ].forEach((row) => {
    const r = document.createElement('div');
    r.className = 'fugassa-move-row';
    row.forEach((label) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'fugassa-btn fugassa-btn--sm fugassa-move-btn';
      b.textContent = label;
      if (label === 'N') b.addEventListener('click', () => actions.move?.('north'));
      if (label === 'S') b.addEventListener('click', () => actions.move?.('south'));
      if (label === 'E') b.addEventListener('click', () => actions.move?.('east'));
      if (label === 'W') b.addEventListener('click', () => actions.move?.('west'));
      b.disabled = !label || label === '·';
      r.appendChild(b);
    });
    moveGrid.appendChild(r);
  });
  const moveSec = section('Move', '');
  moveSec.appendChild(moveGrid);
  wrap.appendChild(moveSec);

  const winRow = document.createElement('div');
  winRow.className = 'fugassa-inline-actions';
  winRow.append(
    btn('Inventory', () => actions.openInventory?.()),
    btn('Crafting', () => actions.openCrafting?.()),
    btn('Character', () => actions.openCharacter?.()),
    btn('Estates', () => actions.openEstates?.()),
    btn('Location', () => actions.openLocation?.()),
    btn('Summary', () => actions.openSummary?.()),
  );
  const winSec = section('Windows', '');
  winSec.appendChild(winRow);
  wrap.appendChild(winSec);

  const actRow = document.createElement('div');
  actRow.className = 'fugassa-inline-actions';
  actRow.append(
    btn('Investigate', () => openInvestigatePopup(popups.investigateBackdrop, actions)),
    btn('Pick up loot', () => openLootPopup(popups.lootBackdrop, loc, actions)),
    btn('Start combat', () => actions.startCombat?.()),
  );
  const actSec = section('Actions', '');
  actSec.appendChild(actRow);
  wrap.appendChild(actSec);

  wrap.appendChild(section('Location', `
    ${locationLabelHtml(loc)}
    <p class="fugassa-muted fugassa-hud-location-coords">${escapeHtml(playerCoordsText(state))}</p>
  `, { attrs: { 'data-hud-location': '' } }));
  return wrap;
}

const INVESTIGATE_DURATION_OPTIONS = [15, 30, 60, 120];

async function openInvestigatePopup(backdrop, actions) {
  if (!backdrop) return;
  const panel = backdrop.querySelector('[data-investigate-popup]');
  backdrop.hidden = false;
  panel.innerHTML = '<h4>Investigate</h4><p class="fugassa-muted">Loading…</p>';

  let types = [];
  try {
    const res = await actions.getInvestigateOptions?.();
    types = res?.types || [];
  } catch (error) {
    panel.innerHTML = `<h4>Investigate</h4><p class="fugassa-muted">${escapeHtml(error.message || String(error))}</p>
      <div class="fugassa-popup-actions"><button type="button" class="fugassa-btn fugassa-btn--sm" data-inv-cancel>Close</button></div>`;
    panel.querySelector('[data-inv-cancel]').addEventListener('click', () => { backdrop.hidden = true; });
    return;
  }

  const allExhausted = types.length > 0 && types.every((t) => t.exhausted);
  panel.innerHTML = `
    <h4>Investigate</h4>
    ${allExhausted ? '<p class="fugassa-muted">Everything here has already been searched.</p>' : ''}
    <div class="fugassa-popup-checklist" data-inv-types>
      ${types.map((t) => `
        <label class="fugassa-popup-checkbox-row ${t.exhausted ? 'fugassa-popup-row--disabled' : ''}">
          <input type="checkbox" value="${escapeHtml(t.type)}" ${t.exhausted ? 'disabled' : 'checked'} />
          <span>${escapeHtml(t.label)} <span class="fugassa-muted">(DC ${t.dc})</span></span>
          ${t.exhausted ? '<span class="fugassa-tag">already searched</span>' : ''}
        </label>
      `).join('')}
    </div>
    <label class="fugassa-popup-field">
      Duration
      <select data-inv-duration>
        ${INVESTIGATE_DURATION_OPTIONS.map((m) => `<option value="${m}" ${m === 30 ? 'selected' : ''}>${m} min</option>`).join('')}
      </select>
    </label>
    <div class="fugassa-popup-actions">
      <button type="button" class="fugassa-btn fugassa-btn--sm" data-inv-cancel>Cancel</button>
      <button type="button" class="fugassa-btn fugassa-btn--sm fugassa-btn--primary" data-inv-submit ${allExhausted ? 'disabled' : ''}>Search</button>
    </div>
  `;
  panel.querySelector('[data-inv-cancel]').addEventListener('click', () => { backdrop.hidden = true; });
  const submitBtn = panel.querySelector('[data-inv-submit]');
  submitBtn?.addEventListener('click', () => {
    const searchTypes = Array.from(panel.querySelectorAll('[data-inv-types] input:checked')).map((i) => i.value);
    const durationMinutes = Number(panel.querySelector('[data-inv-duration]')?.value || 30);
    backdrop.hidden = true;
    if (!searchTypes.length) return;
    actions.investigate?.({ searchTypes, durationMinutes });
  });
}

function openLootPopup(backdrop, loc, actions) {
  if (!backdrop) return;
  const panel = backdrop.querySelector('[data-loot-popup]');
  const loot = (loc.loot || []).map((item) =>
    typeof item === 'object' && item !== null ? item : { name: String(item), qty: 1 },
  );
  backdrop.hidden = false;

  if (!loot.length) {
    panel.innerHTML = `
      <h4>Pick up loot</h4>
      <p class="fugassa-muted">Nothing here to pick up.</p>
      <div class="fugassa-popup-actions"><button type="button" class="fugassa-btn fugassa-btn--sm" data-loot-cancel>Close</button></div>
    `;
    panel.querySelector('[data-loot-cancel]').addEventListener('click', () => { backdrop.hidden = true; });
    return;
  }

  panel.innerHTML = `
    <h4>Pick up loot</h4>
    <div class="fugassa-popup-checklist" data-loot-items>
      ${loot.map((item, idx) => `
        <label class="fugassa-popup-checkbox-row">
          <input type="checkbox" data-loot-check="${idx}" checked />
          <span>${escapeHtml(item.name)}${item.description ? ` <span class="fugassa-muted">— ${escapeHtml(item.description)}</span>` : ''}</span>
          <input type="number" data-loot-qty="${idx}" value="${Math.max(1, Number(item.qty || 1))}" min="1" max="${Math.max(1, Number(item.qty || 1))}" class="fugassa-popup-qty" />
        </label>
      `).join('')}
    </div>
    <div class="fugassa-popup-actions">
      <button type="button" class="fugassa-btn fugassa-btn--sm" data-loot-cancel>Cancel</button>
      <button type="button" class="fugassa-btn fugassa-btn--sm fugassa-btn--primary" data-loot-submit>Pick up selected</button>
    </div>
  `;
  panel.querySelector('[data-loot-cancel]').addEventListener('click', () => { backdrop.hidden = true; });
  panel.querySelector('[data-loot-submit]').addEventListener('click', () => {
    const items = [];
    loot.forEach((item, idx) => {
      const check = panel.querySelector(`[data-loot-check="${idx}"]`);
      const qtyInput = panel.querySelector(`[data-loot-qty="${idx}"]`);
      if (!check?.checked) return;
      items.push({ name: item.name, qty: Math.max(1, Number(qtyInput?.value || 1)) });
    });
    backdrop.hidden = true;
    if (!items.length) return;
    actions.pickupLoot?.({ items });
  });
}

function renderCombatPanel(state, actions) {
  const wrap = document.createElement('div');
  wrap.className = 'fugassa-hud-side-stack';
  const order = (state?.initiative_order || []).join(' → ') || '—';
  wrap.appendChild(section('Initiative', `<p>${escapeHtml(order)}</p>`));

  const actRow = document.createElement('div');
  actRow.className = 'fugassa-inline-actions';
  ['Attack', 'Defend', 'Cast', 'Flee'].forEach((label) => {
    actRow.appendChild(btn(label, () => actions.combatAction?.(label)));
  });
  const combatActSec = section('Combat actions', '');
  combatActSec.appendChild(actRow);
  wrap.appendChild(combatActSec);

  const customSec = section('Custom', '');
  const custom = document.createElement('form');
  custom.className = 'fugassa-hud-combat-custom';
  custom.innerHTML = `
    <input type="text" placeholder="Custom combat action…" class="fugassa-hud-chat-input" />
    <button type="submit" class="fugassa-btn fugassa-btn--primary fugassa-btn--sm">Send</button>
  `;
  custom.addEventListener('submit', (ev) => {
    ev.preventDefault();
    const input = custom.querySelector('input');
    const text = input.value.trim();
    if (!text) return;
    actions.combatAction?.(text);
    input.value = '';
  });
  customSec.appendChild(custom);
  wrap.appendChild(customSec);
  wrap.appendChild(btn('End combat', () => actions.endCombat?.()));
  return wrap;
}

const HEX_AXES = ['kindness', 'empathy', 'wit', 'drive', 'boldness', 'composure'];

async function openNpcDetail(detailSlot, npc, saveId, { onPipelineActivity, onExpandChange, setPortraitEditor, onNpcPortraitReady }) {
  detailSlot.hidden = false;
  detailSlot.innerHTML = '<p class="fugassa-muted">Loading…</p>';
  onExpandChange?.(npc.npc_id);
  try {
    const detail = await api.getNpcDetail(saveId, npc.npc_id);
    detailSlot.innerHTML = `<div data-npc-portrait></div>${renderNpcDetailHtml(detail)}`;
    setPortraitEditor?.(null);
    const ctrl = await mountAssetEditor(detailSlot.querySelector('[data-npc-portrait]'), {
      saveId,
      entityType: 'npc',
      entityId: npc.npc_id,
      assetType: 'portrait',
      title: 'Portrait',
      defaultPositivePrompt: detail?.npc?.portrait_prompt || '',
      onPipelineActivity,
      onAssetReady: ({ entityId, filePath }) => onNpcPortraitReady?.({ entityId, filePath }),
    });
    setPortraitEditor?.(ctrl);
  } catch (error) {
    setPortraitEditor?.(null);
    detailSlot.innerHTML = `<p class="fugassa-muted">${escapeHtml(error.message || String(error))}</p>`;
  }
}

function renderNpcPanel(loc, actions, saveId, {
  onPipelineActivity,
  expandedNpcId: initialExpandedId,
  onExpandChange,
  setPortraitEditor,
  onNpcPortraitReady,
} = {}) {
  const wrap = document.createElement('div');
  wrap.className = 'fugassa-hud-side-stack';
  const npcDetails = loc.npc_details || [];
  const npcs = npcDetails.length ? npcDetails : (loc.npcs || []).map((n) => ({ name: typeof n === 'object' ? n.name : n }));

  if (!npcs.length) {
    wrap.appendChild(section('NPCs', '<p class="fugassa-muted">No NPCs nearby.</p>'));
    return wrap;
  }

  npcs.forEach((npc) => {
    const name = npc.name;
    const card = document.createElement('section');
    card.className = 'fugassa-hud-side-section fugassa-npc-card';
    if (npc.npc_id) card.dataset.npcId = String(npc.npc_id);
    const tags = (npc.tags || []).map((t) => `<span class="fugassa-tag">${escapeHtml(t)}</span>`).join(' ');
    const portraitPath = String(npc.portrait_path || '').trim();
    const portraitUrl = portraitPath && saveId
      ? `/api/fugassa/saves/${encodeURIComponent(saveId)}/assets/${encodeURIComponent(portraitPath)}`
      : '';
    const thumb = portraitUrl
      ? `<img class="fugassa-npc-thumb" src="${portraitUrl}" alt="" loading="lazy" />`
      : '<div class="fugassa-npc-thumb fugassa-npc-thumb--empty" aria-hidden="true"></div>';
    card.innerHTML = `
      <div class="fugassa-npc-card-head">
        ${thumb}
        <div class="fugassa-npc-card-copy">
          <h4>${escapeHtml(name)}${npc.is_hostile ? ' ⚔' : ''}</h4>
          <p class="fugassa-npc-tags">${tags}</p>
          <div class="fugassa-npc-card-actions"></div>
        </div>
      </div>`;
    const detailSlot = document.createElement('div');
    detailSlot.className = 'fugassa-npc-detail';
    detailSlot.hidden = true;
    card.appendChild(detailSlot);

    const rowActions = card.querySelector('.fugassa-npc-card-actions');
    rowActions.className = 'fugassa-inline-actions fugassa-npc-card-actions';
    rowActions.appendChild(btn(`Talk`, () => actions.npcTalk?.(name)));
    if (npc.npc_id && saveId) {
      rowActions.appendChild(
        btn('Detail', async () => {
          if (!detailSlot.hidden) {
            detailSlot.hidden = true;
            onExpandChange?.(null);
            setPortraitEditor?.(null);
            return;
          }
          await openNpcDetail(detailSlot, npc, saveId, {
            onPipelineActivity,
            onExpandChange,
            setPortraitEditor,
            onNpcPortraitReady,
          });
        }),
      );
      if (initialExpandedId && npc.npc_id === initialExpandedId) {
        openNpcDetail(detailSlot, npc, saveId, {
          onPipelineActivity,
          onExpandChange,
          setPortraitEditor,
          onNpcPortraitReady,
        });
      }
    }
    wrap.appendChild(card);
  });

  wrap.appendChild(section('Tip', '<p class="fugassa-muted">Use chat for dialogue — combat actions stay in the Combat tab.</p>'));
  return wrap;
}

function portraitAssetUrl(saveId, portraitPath, { bustCache = false } = {}) {
  const path = String(portraitPath || '').trim();
  if (!path || !saveId) return '';
  const base = `/api/fugassa/saves/${encodeURIComponent(saveId)}/assets/${encodeURIComponent(path)}`;
  return bustCache ? `${base}?v=${Date.now()}` : base;
}

function updateNpcCardPortrait(card, saveId, portraitPath) {
  if (!card) return;
  const head = card.querySelector('.fugassa-npc-card-head');
  if (!head) return;
  let thumb = head.querySelector('.fugassa-npc-thumb');
  const url = portraitAssetUrl(saveId, portraitPath, { bustCache: true });
  if (!url) {
    if (thumb?.tagName === 'IMG') {
      const empty = document.createElement('div');
      empty.className = 'fugassa-npc-thumb fugassa-npc-thumb--empty';
      empty.setAttribute('aria-hidden', 'true');
      thumb.replaceWith(empty);
    }
    return;
  }
  if (thumb?.tagName === 'IMG') {
    thumb.src = url;
    return;
  }
  const img = document.createElement('img');
  img.className = 'fugassa-npc-thumb';
  img.src = url;
  img.alt = '';
  img.loading = 'lazy';
  if (thumb) {
    thumb.replaceWith(img);
  } else {
    head.prepend(img);
  }
}

function renderNpcDetailHtml(detail) {
  if (!detail) return '<p class="fugassa-muted">No data.</p>';
  const hex = detail.hexagon || {};
  const stats = detail.stats || {};
  const rel = detail.relationship || {};
  const hexBars = HEX_AXES.map((axis) => {
    const v = Number(hex[axis] || 0);
    const pct = ((v + 3) / 6) * 100;
    return `<div class="fugassa-hex-row"><span>${axis}</span><div class="fugassa-hex-bar"><div class="fugassa-hex-fill" style="width:${pct}%"></div></div><span>${v >= 0 ? '+' : ''}${v}</span></div>`;
  }).join('');
  const skills = (detail.skills || []).map((s) => `${escapeHtml(s.skill_name)} +${s.bonus}`).join(', ') || '—';
  const goals = (detail.goals || []).map((g) => `<li>${escapeHtml(g.goal_text)}</li>`).join('') || '<li class="fugassa-muted">Unknown</li>';
  return `
    <div class="fugassa-npc-hexagon">${hexBars}</div>
    <p class="fugassa-muted">AC ${stats.armor_class ?? '—'} · HP ${stats.hit_points_current ?? '—'}/${stats.hit_points_max ?? '—'} · CR ${stats.challenge_rating ?? '—'} · ${escapeHtml(stats.combat_stance || '')}</p>
    <p class="fugassa-muted">Skills: ${escapeHtml(skills)}</p>
    <p class="fugassa-muted">Attitude: ${escapeHtml(rel.attitude || 'neutral')} (trust ${rel.trust ?? 0})</p>
    <ul class="fugassa-npc-goals">${goals}</ul>
  `;
}

const QUEST_OBJECTIVE_FALLBACK = {
  visit_location: 'Reach the destination',
  explore: 'Explore the area',
  obtain_item: 'Obtain the required item',
  talk_to_npc: 'Speak with the contact',
  defeat_enemy: 'Defeat the enemy',
  custom: 'Complete the objective',
};

function questObjectiveLabel(objective) {
  const text = String(objective?.text || '').trim();
  if (text) return text;
  const type = String(objective?.objective_type || 'custom').trim();
  return QUEST_OBJECTIVE_FALLBACK[type] || QUEST_OBJECTIVE_FALLBACK.custom;
}

function visibleQuestObjectives(objectives) {
  return (objectives || []).filter((o) => !o?.hidden);
}

function renderQuestPanel(state) {
  const wrap = document.createElement('div');
  wrap.className = 'fugassa-hud-side-stack fugassa-quest-stack';
  const quests = (state?.quests && state.quests.active) || [];
  if (!quests.length) {
    wrap.appendChild(section('Active quests', '<p class="fugassa-muted">No active quests.</p>'));
    return wrap;
  }
  quests.forEach((q) => {
    const card = document.createElement('article');
    card.className = 'fugassa-quest-card';
    const description = String(q.description || q.objective || '').trim();
    const reward = String(q.rewards_preview || '').trim();
    const chain = String(q.chain_code || '').trim();
    const scale = String(q.scale || '').trim();
    const metaParts = [];
    if (scale) metaParts.push(scale);
    if (chain) metaParts.push(`chain ${chain}`);
    if (q.rewards_deferred) metaParts.push('reward later');
    const metaLine = metaParts.length
      ? `<p class="fugassa-quest-meta fugassa-muted">${escapeHtml(metaParts.join(' · '))}</p>`
      : '';
    const rewardLine = reward
      ? `<p class="fugassa-quest-reward fugassa-muted"><strong>Reward:</strong> ${escapeHtml(reward)}</p>`
      : '';
    const visible = visibleQuestObjectives(q.objectives);
    const objectives = visible.length
      ? visible.map((o) => {
        const done = o.status === 'complete';
        const label = questObjectiveLabel(o);
        return `<li class="fugassa-quest-objective${done ? ' fugassa-obj-done' : ''}">${done ? '✔' : '○'} ${escapeHtml(label)}${o.optional ? ' <em>(optional)</em>' : ''}</li>`;
      }).join('')
      : `<li class="fugassa-muted">${escapeHtml(questObjectiveLabel({ text: q.objective || q.description }))}</li>`;
    card.innerHTML = `
      <h4 class="fugassa-quest-title">${escapeHtml(q.name || 'Quest')}</h4>
      ${metaLine}
      ${description ? `<p class="fugassa-quest-desc fugassa-muted">${escapeHtml(description)}</p>` : ''}
      ${rewardLine}
      <ul class="fugassa-quest-objectives">${objectives}</ul>
    `;
    wrap.appendChild(card);
  });
  return wrap;
}

function section(title, html, { attrs } = {}) {
  const sec = document.createElement('section');
  sec.className = 'fugassa-hud-side-section';
  if (attrs) {
    Object.entries(attrs).forEach(([key, val]) => sec.setAttribute(key, val));
  }
  sec.innerHTML = `<h4>${escapeHtml(title)}</h4>${html}`;
  return sec;
}

function btn(label, onClick) {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = 'fugassa-btn fugassa-btn--sm';
  b.textContent = label;
  b.addEventListener('click', onClick);
  return b;
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}
