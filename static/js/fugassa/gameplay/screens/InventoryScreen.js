import * as api from '../../fugassaApi.js';
import uiModule from '../../../ui.js';
import * as equipmentSlots from '../equipmentSlots.js';
import {
  backpackGearFromState,
  itemKindClass,
  kindBadgeModifier,
  renderWalletHtml,
  resolveItemDisplayKind,
  slotCategoryClass,
  walletFromState,
  resolveDeedPropertyCode,
} from '../inventoryDisplay.js';

function menuBtn(label, variant = 'ghost') {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = `fugassa-btn fugassa-btn--${variant} fugassa-btn--sm`;
  b.textContent = label;
  return b;
}

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

function portraitUrlForMember(member, saveId) {
  const file = member?.portrait_file;
  if (!file || !saveId) return null;
  return `/api/fugassa/saves/${encodeURIComponent(saveId)}/assets/${encodeURIComponent(file)}`;
}

function renderMemberPortrait(member, saveId, { large = false } = {}) {
  const name = member?.name || 'Companion';
  const url = portraitUrlForMember(member, saveId);
  if (url) {
    return `<img class="fugassa-inv-companion-portrait${large ? ' fugassa-inv-companion-portrait--lg' : ''}" src="${url}" alt="" />`;
  }
  return `<div class="fugassa-inv-companion-portrait fugassa-inv-companion-portrait--placeholder${large ? ' fugassa-inv-companion-portrait--lg' : ''}">${escapeHtml(name.charAt(0).toUpperCase())}</div>`;
}

// Paperdoll layout: a body-shaped grid of drop targets, not an image —
// arranged so weapons flank the torso (one per hand), gear that layers on
// top of the body (clothes/backpack) sits around the chest, and
// head/belt/feet trace the silhouette top-to-bottom.
const PAPERDOLL_LAYOUT = [
  [null, 'head', null],
  ['weapon_off', 'body', 'weapon_main'],
  ['hands', 'clothes', 'backpack'],
  [null, 'belt', null],
  [null, 'feet', null],
];

export function mountInventoryScreen(root, {
  state,
  saveId,
  onClose,
  onStateChange,
  onOpenEstates,
  onOpenCharacter,
}) {
  root.className = 'fugassa-screen fugassa-screen--inventory';
  const party = state?.party || [];
  let heroIdx = 0;
  let dragged = null; // { item, from: {kind:'shared', idx} | {kind:'slot', slot} }
  let detailItem = null; // { item, source } shown in the detail overlay

  const currentInventory = () => state?.inventory || {};
  const heroName = () => party[heroIdx]?.name || 'Hero';
  const activeMember = () => party[heroIdx] || { name: 'Hero' };
  const isCompanionTab = () => heroIdx > 0;

  async function refreshFromServer(res) {
    state = res.state ?? state;
    onStateChange?.(state);
    render();
  }

  const render = () => {
    const inv = currentInventory();
    const shared = backpackGearFromState(state);
    const equipped = inv.equipped?.[heroName()] || {};
    const wallet = walletFromState(state);
    const member = activeMember();
    const companionView = isCompanionTab();

    root.innerHTML = `
      <header class="fugassa-screen-head">
        <h2>Inventory &amp; Equipment</h2>
        <button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-close>Back to game</button>
      </header>
      <div class="fugassa-screen-body fugassa-inv-layout-v2">
        ${wallet.length ? `<section class="fugassa-screen-card fugassa-wallet-card"><h3>Currency</h3>${renderWalletHtml(wallet, escapeHtml)}</section>` : '<section class="fugassa-screen-card fugassa-wallet-card"><h3>Currency</h3><p class="fugassa-muted">No currency tiers configured for this campaign.</p></section>'}
        <section class="fugassa-screen-card fugassa-inv-party-card">
          <h3>Party</h3>
          <div class="fugassa-inv-party-tabs" data-party-tabs></div>
        </section>
        <section class="fugassa-screen-card fugassa-paperdoll-card">
          <h3>Equipped — ${escapeHtml(heroName())}</h3>
          ${companionView
            ? `<div class="fugassa-inv-companion-panel" data-companion-panel></div>`
            : `<p class="fugassa-muted">Drag items from the backpack onto a slot. Only matching gear fits — a potion can't go in the armor slot.</p>
               <div class="fugassa-paperdoll" data-paperdoll></div>`}
        </section>
        <section class="fugassa-screen-card fugassa-backpack-card">
          <h3>Backpack</h3>
          <p class="fugassa-muted">${companionView ? 'Shared party inventory — only the hero can equip items.' : 'Click an item for details. Drag onto a slot to equip.'}</p>
          <div class="fugassa-inv-grid${companionView ? ' fugassa-inv-grid--readonly' : ''}" data-shared></div>
        </section>
      </div>
      <div class="fugassa-item-detail-backdrop" data-detail-backdrop hidden>
        <div class="fugassa-item-detail" data-detail-panel></div>
      </div>
    `;

    root.querySelector('[data-close]').addEventListener('click', () => onClose?.());
    root.querySelector('[data-detail-backdrop]').addEventListener('click', (ev) => {
      if (ev.target.matches('[data-detail-backdrop]')) closeDetail();
    });

    const tabs = root.querySelector('[data-party-tabs]');
    if (!party.length) {
      tabs.innerHTML = '<span class="fugassa-muted">No party members</span>';
    } else {
      party.forEach((m, idx) => {
        const b = document.createElement('button');
        b.type = 'button';
        b.className = `fugassa-inv-party-tab${idx === heroIdx ? ' is-active' : ''}`;
        b.innerHTML = `
          ${renderMemberPortrait(m, saveId)}
          <span>${escapeHtml(m.name || `Hero ${idx + 1}`)}</span>
        `;
        b.addEventListener('click', () => {
          heroIdx = idx;
          render();
        });
        tabs.appendChild(b);
      });
    }

    if (companionView) {
      const panel = root.querySelector('[data-companion-panel]');
      panel.innerHTML = `
        <div class="fugassa-inv-companion-head">
          ${renderMemberPortrait(member, saveId, { large: true })}
          <div>
            <strong>${escapeHtml(member.name || 'Companion')}</strong>
            <p class="fugassa-muted">${escapeHtml(member.race || '')} ${escapeHtml(member.character_class || member.role || 'companion')} · HP ${Number(member.hp ?? 0)}/${Number(member.max_hp ?? 0)}</p>
            ${member.backstory_summary ? `<p>${escapeHtml(member.backstory_summary)}</p>` : '<p class="fugassa-muted">Companion gear is not managed here.</p>'}
          </div>
        </div>
        <div class="fugassa-paperdoll fugassa-paperdoll--disabled" aria-disabled="true">
          ${PAPERDOLL_LAYOUT.flat().filter(Boolean).map((slotKey) => `
            <div class="fugassa-slot fugassa-slot--empty fugassa-slot--disabled">
              <span class="fugassa-slot-label">${escapeHtml(equipmentSlots.SLOT_LABELS[slotKey] || slotKey)}</span>
              <span class="fugassa-slot-item">—</span>
            </div>
          `).join('')}
        </div>
        <div class="fugassa-inline-actions">
          <button type="button" class="fugassa-btn fugassa-btn--primary fugassa-btn--sm" data-view-character>View character</button>
        </div>
      `;
      panel.querySelector('[data-view-character]')?.addEventListener('click', () => {
        onOpenCharacter?.(heroIdx);
      });
    } else {
      renderPaperdoll(equipped);
    }

    renderBackpack(shared, companionView);
    renderDetailOverlay(companionView);
  };

  const renderPaperdoll = (equipped) => {
    const doll = root.querySelector('[data-paperdoll]');
    if (!doll) return;
    doll.innerHTML = '';
    PAPERDOLL_LAYOUT.forEach((row) => {
      row.forEach((slotKey) => {
        if (!slotKey) {
          const spacer = document.createElement('div');
          spacer.className = 'fugassa-slot fugassa-slot--spacer';
          doll.appendChild(spacer);
          return;
        }
        const item = equipped[slotKey];
        const slotEl = document.createElement('div');
        slotEl.className = [
          'fugassa-slot',
          slotCategoryClass(slotKey, equipmentSlots.slotCategory),
          item ? 'fugassa-slot--filled' : 'fugassa-slot--empty',
        ].join(' ');
        slotEl.dataset.slot = slotKey;
        slotEl.innerHTML = `
          <span class="fugassa-slot-label">${escapeHtml(equipmentSlots.SLOT_LABELS[slotKey] || slotKey)}</span>
          <span class="fugassa-slot-item">${item ? escapeHtml(item.name) : '—'}</span>
        `;
        if (item) {
          slotEl.draggable = true;
          slotEl.addEventListener('dragstart', (ev) => {
            dragged = { item, from: { kind: 'slot', slot: slotKey } };
            ev.dataTransfer.effectAllowed = 'move';
            ev.dataTransfer.setData('text/plain', item.name || '');
          });
          slotEl.addEventListener('dragend', () => {
            dragged = null;
            clearDropHighlights();
          });
          slotEl.addEventListener('click', () => openDetail(item, { kind: 'slot', slot: slotKey }));
        }
        slotEl.addEventListener('dragover', (ev) => {
          if (!dragged) return;
          ev.preventDefault();
          const ok = equipmentSlots.slotAccepts(slotKey, dragged.item) && !(dragged.from.kind === 'slot' && dragged.from.slot === slotKey);
          slotEl.classList.toggle('fugassa-slot--valid', ok);
          slotEl.classList.toggle('fugassa-slot--invalid', !ok);
        });
        slotEl.addEventListener('dragleave', () => {
          slotEl.classList.remove('fugassa-slot--valid', 'fugassa-slot--invalid');
        });
        slotEl.addEventListener('drop', async (ev) => {
          ev.preventDefault();
          slotEl.classList.remove('fugassa-slot--valid', 'fugassa-slot--invalid');
          if (!dragged) return;
          const { item: droppedItem, from } = dragged;
          dragged = null;
          if (from.kind === 'slot' && from.slot === slotKey) return;
          if (!equipmentSlots.slotAccepts(slotKey, droppedItem)) {
            uiModule.showToast?.(`${droppedItem.name} can't be equipped in the ${equipmentSlots.SLOT_LABELS[slotKey] || slotKey} slot.`, { duration: 3200 });
            return;
          }
          await doEquip(droppedItem.name, slotKey, from);
        });
        doll.appendChild(slotEl);
      });
    });
  };

  const renderBackpack = (shared, readOnly) => {
    const grid = root.querySelector('[data-shared]');
    if (!shared.length) {
      grid.innerHTML = '<p class="fugassa-muted">No items in the backpack.</p>';
      return;
    }
    grid.innerHTML = '';
    shared.forEach((item) => {
      const display = resolveItemDisplayKind(item, equipmentSlots.classifyItemCategory);
      const card = document.createElement('button');
      card.type = 'button';
      card.className = [
        'fugassa-inv-item',
        'fugassa-inv-item--clickable',
        itemKindClass(display.kind),
      ].join(' ');
      if (!readOnly) card.draggable = true;
      card.innerHTML = `
        <div class="fugassa-inv-item-meta">
          <span class="fugassa-inv-item-kind ${kindBadgeModifier(display.kind)}">${escapeHtml(display.label)}</span>
          <span class="fugassa-inv-qty">×${Number(item.qty || 1)}</span>
        </div>
        <strong class="fugassa-inv-item-name">${escapeHtml(item.name)}</strong>
      `;
      card.addEventListener('click', () => openDetail(item, { kind: 'shared' }));
      if (!readOnly) {
        card.addEventListener('dragstart', (ev) => {
          dragged = { item, from: { kind: 'shared' } };
          ev.dataTransfer.effectAllowed = 'move';
          ev.dataTransfer.setData('text/plain', item.name || '');
        });
        card.addEventListener('dragend', () => {
          dragged = null;
          clearDropHighlights();
        });
      }
      grid.appendChild(card);
    });
    if (readOnly) return;
    grid.addEventListener('dragover', (ev) => {
      if (!dragged || dragged.from.kind !== 'slot') return;
      ev.preventDefault();
      grid.classList.add('fugassa-inv-grid--drop-target');
    });
    grid.addEventListener('dragleave', () => grid.classList.remove('fugassa-inv-grid--drop-target'));
    grid.addEventListener('drop', async (ev) => {
      grid.classList.remove('fugassa-inv-grid--drop-target');
      if (!dragged || dragged.from.kind !== 'slot') return;
      ev.preventDefault();
      const slot = dragged.from.slot;
      dragged = null;
      await doUnequip(slot);
    });
  };

  const clearDropHighlights = () => {
    root.querySelectorAll('.fugassa-slot--valid, .fugassa-slot--invalid').forEach((el) => {
      el.classList.remove('fugassa-slot--valid', 'fugassa-slot--invalid');
    });
    root.querySelector('[data-shared]')?.classList.remove('fugassa-inv-grid--drop-target');
  };

  const openDetail = (item, source) => {
    detailItem = { item, source };
    renderDetailOverlay(isCompanionTab());
  };

  const closeDetail = () => {
    detailItem = null;
    renderDetailOverlay(isCompanionTab());
  };

  const renderDetailOverlay = (readOnly) => {
    const backdrop = root.querySelector('[data-detail-backdrop]');
    const panel = root.querySelector('[data-detail-panel]');
    if (!backdrop || !panel) return;
    if (!detailItem) {
      backdrop.hidden = true;
      panel.innerHTML = '';
      return;
    }
    const { item, source } = detailItem;
    const display = resolveItemDisplayKind(item, equipmentSlots.classifyItemCategory);
    const category = equipmentSlots.classifyItemCategory(item);
    const candidateSlots = equipmentSlots.slotsForItem(item);
    backdrop.hidden = false;
    panel.innerHTML = `
      <div class="fugassa-item-detail-head">
        <h3>${escapeHtml(item.name)}</h3>
        <span class="fugassa-inv-item-kind ${kindBadgeModifier(display.kind)}">${escapeHtml(display.label)}</span>
      </div>
      <p class="fugassa-muted">${escapeHtml(item.description || 'No description.')}</p>
      ${source.kind === 'shared' ? `<p class="fugassa-muted">Quantity: ${Number(item.qty || 1)}</p>` : ''}
      <p class="fugassa-muted">${category ? `Fits: ${candidateSlots.map((s) => equipmentSlots.SLOT_LABELS[s]).join(' or ')}` : display.equippable ? 'Equippable.' : 'Not equippable — use from backpack.'}</p>
      <div class="fugassa-inline-actions" data-detail-actions></div>
    `;
    const actions = panel.querySelector('[data-detail-actions]');
    const deedCode = resolveDeedPropertyCode(item);
    if (deedCode && onOpenEstates) {
      const b = menuBtn('View in Estates', 'primary');
      b.addEventListener('click', () => {
        closeDetail();
        onClose?.();
        onOpenEstates();
      });
      actions.appendChild(b);
    }
    if (!readOnly) {
      if (source.kind === 'shared') {
        candidateSlots.forEach((slot) => {
          const b = menuBtn(`Equip to ${equipmentSlots.SLOT_LABELS[slot]}`, 'primary');
          b.addEventListener('click', async () => {
            closeDetail();
            await doEquip(item.name, slot, { kind: 'shared' });
          });
          actions.appendChild(b);
        });
      } else if (source.kind === 'slot') {
        const b = menuBtn('Unequip', 'ghost');
        b.addEventListener('click', async () => {
          closeDetail();
          await doUnequip(source.slot);
        });
        actions.appendChild(b);
      }
    }
    const closeBtn = menuBtn('Close', 'ghost');
    closeBtn.addEventListener('click', closeDetail);
    actions.appendChild(closeBtn);
  };

  async function doEquip(itemName, slot, from) {
    try {
      if (from?.kind === 'slot') {
        await api.unequipItem(saveId, { heroName: heroName(), slot: from.slot });
      }
      const res = await api.equipItem(saveId, { heroName: heroName(), itemName, slot });
      await refreshFromServer(res);
      uiModule.showToast?.(`Equipped ${itemName}.`, { duration: 1800, leadingIcon: 'check' });
    } catch (error) {
      uiModule.showToast?.(error.message || String(error), { duration: 3500 });
      render();
    }
  }

  async function doUnequip(slot) {
    try {
      const res = await api.unequipItem(saveId, { heroName: heroName(), slot });
      await refreshFromServer(res);
      uiModule.showToast?.('Unequipped.', { duration: 1500 });
    } catch (error) {
      uiModule.showToast?.(error.message || String(error), { duration: 3500 });
      render();
    }
  }

  render();
}

export { menuBtn, escapeHtml };
