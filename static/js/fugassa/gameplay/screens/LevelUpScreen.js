import * as api from '../../fugassaApi.js';
import { escapeHtml } from './InventoryScreen.js';

function renderPickers(pickers, choices, onToggle) {
  if (!pickers.length) {
    return '<p class="fugassa-muted">No class mechanic choices at this level.</p>';
  }
  return pickers.map((picker) => {
    const pid = picker.id;
    const cap = Number(picker.cap || 0);
    const picked = choices[pid] || [];
    const ptype = picker.type || 'enum';
    if (ptype === 'enum') {
      const opts = (picker.options || []).map((opt) => (
        `<option value="${escapeHtml(opt.id)}"${picked[0] === opt.id ? ' selected' : ''}>${escapeHtml(opt.name || opt.id)}</option>`
      )).join('');
      return `
        <div class="fugassa-class-mechanic-block" data-picker="${escapeHtml(pid)}">
          <strong>${escapeHtml(picker.label || pid)}</strong>
          <select class="fugassa-input" data-enum-picker="${escapeHtml(pid)}">
            <option value="">— pick one —</option>
            ${opts}
          </select>
        </div>`;
    }
    const buttons = (picker.options || []).map((opt) => {
      const active = picked.includes(opt.id) ? ' is-active' : '';
      return `<button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm${active}" data-multi-pick="${escapeHtml(pid)}" data-opt="${escapeHtml(opt.id)}">${escapeHtml(opt.name || opt.id)}</button>`;
    }).join('');
    return `
      <div class="fugassa-class-mechanic-block">
        <strong>${escapeHtml(picker.label || pid)} (${picked.length}/${cap})</strong>
        <p class="fugassa-muted">${escapeHtml(picker.hint || '')}</p>
        <div class="fugassa-spell-grid">${buttons}</div>
      </div>`;
  }).join('');
}

export async function mountLevelUpScreen(root, { saveId, state, onClose, onApplied }) {
  root.className = 'fugassa-screen fugassa-screen--level-up';
  const identity = state?.character_sheet?.stable_sheet?.identity || {};
  const currentLevel = Number(identity.level || 1);
  const targetLevel = Math.min(20, currentLevel + 1);
  let choices = { ...(state?.wizard_draft_snapshot?.class_mechanic_choices || {}) };
  let preview = null;

  root.innerHTML = `
    <header class="fugassa-screen-head">
      <h2>Level up → ${targetLevel}</h2>
      <button type="button" class="fugassa-btn fugassa-btn--ghost fugassa-btn--sm" data-close>Cancel</button>
    </header>
    <div class="fugassa-screen-body" data-body>
      <p class="fugassa-muted">Loading level-up preview…</p>
    </div>
  `;

  root.querySelector('[data-close]').addEventListener('click', () => onClose?.());

  const body = root.querySelector('[data-body]');

  try {
    preview = await api.previewLevelUp(saveId, targetLevel);
  } catch (error) {
    body.innerHTML = `<p class="fugassa-muted">${escapeHtml(error.message || String(error))}</p>`;
    return;
  }

  const pickers = preview.class_mechanic_pickers || [];
  const resourceLines = preview.class_resource_summary || [];
  const hpNew = preview.hp_new;

  function paint() {
    body.innerHTML = `
      <section class="fugassa-screen-card">
        <p>Advance from level <strong>${currentLevel}</strong> to <strong>${targetLevel}</strong>.</p>
        ${hpNew ? `<p class="fugassa-muted">Max HP after level-up: ${hpNew}</p>` : ''}
        ${resourceLines.length ? `<p class="fugassa-muted">${resourceLines.map(escapeHtml).join(' · ')}</p>` : ''}
      </section>
      <section class="fugassa-screen-card">
        <h3>Class mechanics</h3>
        <div data-pickers>${renderPickers(pickers, choices)}</div>
      </section>
      <section class="fugassa-screen-card">
        <button type="button" class="fugassa-btn fugassa-btn--primary" data-apply>Apply level-up</button>
        <p class="fugassa-muted" data-feedback></p>
      </section>
    `;

    body.querySelectorAll('[data-enum-picker]').forEach((sel) => {
      sel.addEventListener('change', () => {
        const pid = sel.dataset.enumPicker;
        choices[pid] = sel.value ? [sel.value] : [];
        paint();
      });
    });

    body.querySelectorAll('[data-multi-pick]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const pid = btn.dataset.multiPick;
        const opt = btn.dataset.opt;
        const picker = pickers.find((p) => p.id === pid);
        const cap = Number(picker?.cap || 0);
        const list = [...(choices[pid] || [])];
        const idx = list.indexOf(opt);
        if (idx >= 0) list.splice(idx, 1);
        else if (list.length < cap) list.push(opt);
        choices[pid] = list;
        paint();
      });
    });

    body.querySelector('[data-apply]').addEventListener('click', async () => {
      const feedback = body.querySelector('[data-feedback]');
      feedback.textContent = 'Applying…';
      try {
        const res = await api.applyLevelUp(saveId, {
          target_level: targetLevel,
          class_mechanic_choices: choices,
          hp_current: hpNew,
        });
        onApplied?.(res.state);
        onClose?.();
      } catch (error) {
        feedback.textContent = error.message || String(error);
      }
    });
  }

  paint();
}
