import * as api from '../../fugassaApi.js';
import uiModule from '../../../ui.js';

function escapeHtml(s) {
  const d = document.createElement('div');
  d.textContent = s == null ? '' : String(s);
  return d.innerHTML;
}

function menuBtn(label, variant = 'ghost') {
  const b = document.createElement('button');
  b.type = 'button';
  b.className = `fugassa-btn fugassa-btn--${variant} fugassa-btn--sm`;
  b.textContent = label;
  return b;
}

const PROFESSIONS = ['weaponsmith', 'armorsmith', 'alchemist', 'enchanter', 'engineer', 'artisan'];
const RANK_NAMES = ['Novice', 'Apprentice', 'Journeyman', 'Expert', 'Master', 'Grandmaster'];

export function mountCraftingScreen(root, { state, saveId, onClose, onStateChange }) {
  root.className = 'fugassa-screen fugassa-screen--crafting';
  const party = state?.party || [];
  let heroIdx = 0;
  let professions = [];
  let blueprints = [];
  let lastResult = null;
  let loading = true;
  let loadError = null;

  const heroName = () => party[heroIdx]?.name || 'Hero';

  async function refresh() {
    loading = true;
    loadError = null;
    render();
    try {
      const [profRes, bpRes] = await Promise.all([
        api.getCraftingProfessions(saveId, heroName()),
        api.getCraftingBlueprints(saveId, heroName()),
      ]);
      professions = profRes.professions || [];
      blueprints = bpRes.blueprints || [];
    } catch (error) {
      loadError = error.message || String(error);
    } finally {
      loading = false;
      render();
    }
  }

  function rankOf(profession) {
    return professions.find((p) => p.profession === profession)?.rank ?? 0;
  }

  function rankNameOf(profession) {
    return professions.find((p) => p.profession === profession)?.rank_name || RANK_NAMES[0];
  }

  const render = () => {
    root.innerHTML = `
      <header class="fugassa-screen-head">
        <h2>Crafting</h2>
        <button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-close>Back to game</button>
      </header>
      <div class="fugassa-screen-body fugassa-crafting-layout">
        <section class="fugassa-screen-card">
          <h3>Party</h3>
          <div class="fugassa-inline-actions" data-party-tabs></div>
        </section>
        <section class="fugassa-screen-card" data-result-card hidden>
          <h3>Last attempt</h3>
          <div data-result></div>
        </section>
        <section class="fugassa-screen-card">
          <h3>Professions — ${escapeHtml(heroName())}</h3>
          <div data-professions></div>
        </section>
        <section class="fugassa-screen-card">
          <h3>Known blueprints</h3>
          <div data-blueprints></div>
        </section>
        <section class="fugassa-screen-card">
          <h3>Invent a new blueprint</h3>
          <p class="fugassa-muted">Design something from scratch. High DC — you only spend time, not materials, and only on success do you actually learn the recipe.</p>
          <form data-invent-form class="fugassa-craft-form"></form>
        </section>
        <section class="fugassa-screen-card">
          <h3>Reverse-engineer an owned item</h3>
          <p class="fugassa-muted">Study an item you're carrying to learn how to make more. The item is consumed either way.</p>
          <form data-reverse-form class="fugassa-craft-form"></form>
        </section>
      </div>
    `;

    root.querySelector('[data-close]').addEventListener('click', () => onClose?.());

    const tabs = root.querySelector('[data-party-tabs]');
    if (!party.length) {
      tabs.innerHTML = '<span class="fugassa-muted">No party members</span>';
    } else {
      party.forEach((member, idx) => {
        const b = menuBtn(member.name || `Hero ${idx + 1}`, idx === heroIdx ? 'primary' : 'ghost');
        b.addEventListener('click', () => {
          if (idx === heroIdx) return;
          heroIdx = idx;
          refresh();
        });
        tabs.appendChild(b);
      });
    }

    renderResult();

    const profWrap = root.querySelector('[data-professions]');
    const bpWrap = root.querySelector('[data-blueprints]');
    if (loading) {
      profWrap.innerHTML = '<p class="fugassa-muted">Loading…</p>';
      bpWrap.innerHTML = '<p class="fugassa-muted">Loading…</p>';
    } else if (loadError) {
      profWrap.innerHTML = `<p class="fugassa-muted">${escapeHtml(loadError)}</p>`;
      bpWrap.innerHTML = '';
    } else {
      renderProfessions(profWrap);
      renderBlueprints(bpWrap);
    }

    renderInventForm();
    renderReverseForm();
  };

  const renderResult = () => {
    const card = root.querySelector('[data-result-card]');
    const slot = root.querySelector('[data-result]');
    if (!lastResult) {
      card.hidden = true;
      return;
    }
    card.hidden = false;
    const r = lastResult;
    const outcome = r.success ? 'success' : 'failure';
    slot.innerHTML = `
      <p class="${r.success ? 'fugassa-craft-success' : 'fugassa-craft-failure'}">
        <strong>${escapeHtml(r.output_item_name || '')}</strong> — ${outcome}
        ${r.critical ? ' (critical!)' : ''}${r.fumble ? ' (fumble)' : ''}
      </p>
      <p class="fugassa-muted">${escapeHtml(r.summary || '')}</p>
    `;
  };

  const renderProfessions = (wrap) => {
    if (!professions.length) {
      wrap.innerHTML = '<p class="fugassa-muted">No profession data.</p>';
      return;
    }
    wrap.innerHTML = '';
    const grid = document.createElement('div');
    grid.className = 'fugassa-profession-grid';
    professions.forEach((p) => {
      const nextThreshold = RANK_XP_HINT(p.rank);
      const row = document.createElement('div');
      row.className = 'fugassa-profession-row';
      row.innerHTML = `
        <span class="fugassa-profession-name">${escapeHtml(capitalize(p.profession))}</span>
        <span class="fugassa-profession-rank">${escapeHtml(p.rank_name)}</span>
        <span class="fugassa-muted">${p.xp} xp${nextThreshold != null ? ` (next at ${nextThreshold})` : ''}</span>
      `;
      grid.appendChild(row);
    });
    wrap.appendChild(grid);
  };

  const renderBlueprints = (wrap) => {
    if (!blueprints.length) {
      wrap.innerHTML = '<p class="fugassa-muted">No known blueprints yet — invent one, reverse-engineer an item, or find one as loot.</p>';
      return;
    }
    wrap.innerHTML = '';
    const byProfession = {};
    blueprints.forEach((bp) => {
      (byProfession[bp.profession] = byProfession[bp.profession] || []).push(bp);
    });
    Object.entries(byProfession).forEach(([profession, recipes]) => {
      const group = document.createElement('div');
      group.className = 'fugassa-blueprint-group';
      group.innerHTML = `<h4>${escapeHtml(capitalize(profession))} — ${escapeHtml(rankNameOf(profession))}</h4>`;
      recipes.forEach((recipe) => {
        const rankTooLow = rankOf(profession) < recipe.min_rank;
        const card = document.createElement('div');
        card.className = 'fugassa-blueprint-card';
        const ingredientsHtml = (recipe.ingredients_status || [])
          .map((i) => `<span class="${i.have_enough ? 'fugassa-ingredient-ok' : 'fugassa-ingredient-missing'}">${escapeHtml(i.item_name)} (${i.qty_have}/${i.qty_needed})</span>`)
          .join(', ');
        card.innerHTML = `
          <div class="fugassa-blueprint-head">
            <strong>${escapeHtml(recipe.output_item_name)}</strong>
            <span class="fugassa-tag">Tier ${recipe.tier}</span>
            <span class="fugassa-tag">DC ${recipe.craft_dc}</span>
          </div>
          ${recipe.description ? `<p class="fugassa-muted">${escapeHtml(recipe.description)}</p>` : ''}
          <p class="fugassa-muted">Needs: ${ingredientsHtml || '—'}</p>
          ${rankTooLow ? `<p class="fugassa-muted">Requires ${escapeHtml(RANK_NAMES[recipe.min_rank])} ${escapeHtml(profession)}.</p>` : ''}
        `;
        const craftBtn = menuBtn('Craft', 'primary');
        craftBtn.disabled = rankTooLow || !recipe.can_afford;
        craftBtn.addEventListener('click', () => doCraft(recipe.code));
        card.appendChild(craftBtn);
        group.appendChild(card);
      });
      wrap.appendChild(group);
    });
  };

  const renderInventForm = () => {
    const form = root.querySelector('[data-invent-form]');
    form.innerHTML = `
      <label>Profession
        <select data-invent-profession>${PROFESSIONS.map((p) => `<option value="${p}">${capitalize(p)}</option>`).join('')}</select>
      </label>
      <label>Target tier
        <select data-invent-tier>${RANK_NAMES.map((name, idx) => `<option value="${idx}">${idx} — ${name}</option>`).join('')}</select>
      </label>
      <label>What do you want to make?
        <input type="text" data-invent-description placeholder="e.g. a lantern that senses undead" maxlength="500" required />
      </label>
      <button type="submit" class="fugassa-btn fugassa-btn--primary fugassa-btn--sm">Attempt invention</button>
    `;
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const profession = form.querySelector('[data-invent-profession]').value;
      const tier = Number(form.querySelector('[data-invent-tier]').value || 0);
      const description = form.querySelector('[data-invent-description]').value.trim();
      if (!description) return;
      await doInvent(profession, tier, description);
    });
  };

  const renderReverseForm = () => {
    const form = root.querySelector('[data-reverse-form]');
    const shared = state?.inventory?.shared || [];
    if (!shared.length) {
      form.innerHTML = '<p class="fugassa-muted">Nothing in your backpack to study.</p>';
      return;
    }
    form.innerHTML = `
      <label>Profession
        <select data-reverse-profession>${PROFESSIONS.map((p) => `<option value="${p}">${capitalize(p)}</option>`).join('')}</select>
      </label>
      <label>Item to study
        <select data-reverse-item>${shared.map((i) => `<option value="${escapeHtml(i.name)}">${escapeHtml(i.name)} ×${Number(i.qty || 1)}</option>`).join('')}</select>
      </label>
      <button type="submit" class="fugassa-btn fugassa-btn--primary fugassa-btn--sm">Attempt reverse-engineering</button>
    `;
    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const profession = form.querySelector('[data-reverse-profession]').value;
      const itemName = form.querySelector('[data-reverse-item]').value;
      if (!itemName) return;
      await doReverseEngineer(profession, itemName);
    });
  };

  async function doCraft(recipeCode) {
    try {
      const res = await api.craftItem(saveId, { heroName: heroName(), recipeCode });
      applyStateAndResult(res.state, res.craft);
      uiModule.showToast?.(res.craft?.summary || 'Craft attempt resolved.', { duration: 3000 });
    } catch (error) {
      uiModule.showToast?.(error.message || String(error), { duration: 3500 });
    }
  }

  async function doInvent(profession, tier, description) {
    try {
      const res = await api.inventBlueprint(saveId, { heroName: heroName(), profession, tier, description });
      applyStateAndResult(res.state, res.invent);
      uiModule.showToast?.(res.invent?.summary || 'Invention attempt resolved.', { duration: 3000 });
    } catch (error) {
      uiModule.showToast?.(error.message || String(error), { duration: 3500 });
    }
  }

  async function doReverseEngineer(profession, itemName) {
    try {
      const res = await api.reverseEngineerItem(saveId, { heroName: heroName(), profession, itemName });
      applyStateAndResult(res.state, res.reverse_engineer);
      uiModule.showToast?.(res.reverse_engineer?.summary || 'Reverse-engineering attempt resolved.', { duration: 3000 });
    } catch (error) {
      uiModule.showToast?.(error.message || String(error), { duration: 3500 });
    }
  }

  function applyStateAndResult(nextState, result) {
    lastResult = result || null;
    if (nextState) {
      state = nextState;
      onStateChange?.(state);
    }
    refresh();
  }

  function capitalize(s) {
    const str = String(s || '');
    return str.charAt(0).toUpperCase() + str.slice(1);
  }

  function RANK_XP_HINT() {
    return null; // XP thresholds are engine-internal; only rank/xp-so-far is shown.
  }

  refresh();
}
