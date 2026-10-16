/**
 * Editor setup checklist ("preflight").
 *
 * Shown over the canvas the first time the image editor opens (and
 * re-openable from the footer "Setup" button). Each row is a setup step
 * with a short how-to and an automatic ✓ / ✕ mark; a manual checkbox lets
 * the user tick a step the detector cannot see. State is persisted in
 * localStorage so the checklist doesn't nag once dismissed.
 */
import {
  resolveQuickEditModel,
  resolveQuickEditAgentModel,
  getAIModelsStatus,
} from './ai-models.js';

const DISMISS_KEY = 'ge-preflight-dismissed';
const OVERRIDE_KEY = 'ge-preflight-done';

export const PREFLIGHT_STEPS = [
  {
    id: 'provider',
    title: 'Image server / AI provider reachable',
    hint: 'The editor sends edits and generations to your enabled image model (local Qwen). '
      + 'This loads automatically — click Retry if it is stale.',
    action: 'retry',
    actionLabel: 'Retry',
  },
  {
    id: 'editModel',
    title: 'Image edit model set',
    hint: 'Open AI settings and pick the image edit model. “Auto” uses the local '
      + 'Qwen-Image-Edit model.',
    action: 'model',
    actionLabel: 'Open settings',
  },
  {
    id: 'agentModel',
    title: 'AI agent (vision) model set',
    hint: 'Optional but recommended. Open AI settings and pick the vision agent — it '
      + 'plans edits and can double-check them when “Double-check AI edits” is on.',
    action: 'agent',
    actionLabel: 'Open settings',
    optional: true,
  },
  {
    id: 'bgRemover',
    title: 'Background remover available',
    hint: 'Removes the background via the image server. Use Quick Edit (“remove the '
      + 'background”) or the toolbar remove-background tool.',
    action: 'retry',
    actionLabel: 'Retry',
  },
];

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function readOverride() {
  try {
    const raw = JSON.parse(localStorage.getItem(OVERRIDE_KEY) || '[]');
    return new Set(Array.isArray(raw) ? raw : []);
  } catch (_) { return new Set(); }
}

function writeOverride(set) {
  try { localStorage.setItem(OVERRIDE_KEY, JSON.stringify([...set])); } catch (_) {}
}

export function createPreflight({ canvasArea }) {
  const el = document.createElement('div');
  el.className = 'ge-preflight';
  el.hidden = true;
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.setAttribute('aria-label', 'Editor setup checklist');

  // Compute automatic status for every step + a short detail line.
  function detect() {
    const s = getAIModelsStatus() || {};
    const models = Array.isArray(s.models) ? s.models : [];
    const usable = models.filter((m) => m && m.usable);
    const provider = !!s.ready && usable.length > 0;

    const edit = resolveQuickEditModel();
    const editModel = !!(edit && (edit.model || edit.endpoint));

    const agentSel = document.getElementById('ge-ai-command-agent-model');
    const agentModel = !!(agentSel && String(agentSel.value || '').trim());

    const bgRemover = provider;

    return {
      provider: {
        done: provider,
        detail: provider
          ? `${usable.length} image model${usable.length === 1 ? '' : 's'} online`
          : (s.error ? `Not reachable — ${s.error}` : 'No image models loaded'),
      },
      editModel: {
        done: editModel,
        detail: editModel ? (edit.label || edit.model) : 'No edit model resolved',
      },
      agentModel: {
        done: agentModel,
        detail: agentModel
          ? ((resolveQuickEditAgentModel() || {}).label || 'Server default')
          : 'No agent model selected',
      },
      bgRemover: {
        done: bgRemover,
        detail: bgRemover ? 'Serviced by the image server' : 'Unavailable while the server is offline',
      },
    };
  }

  function render() {
    const detected = detect();
    const override = readOverride();
    let doneRequired = 0;
    let requiredCount = 0;
    const rows = PREFLIGHT_STEPS.map((step) => {
      const info = detected[step.id] || { done: false, detail: '' };
      const manual = override.has(step.id);
      const done = info.done || manual;
      const optional = !!step.optional;
      if (!optional) { requiredCount += 1; if (done) doneRequired += 1; }
      const stateClass = done ? 'is-done' : (optional ? 'is-optional' : 'is-todo');
      const badge = done ? '✓' : (optional ? '○' : '✕');
      // Only offer an action / manual tick while the step is still missing —
      // otherwise a ✓ row would still say “Retry”, which is confusing.
      const actions = done ? '' : `<div class="ge-preflight-actions">
          <button type="button" class="ge-preflight-act" data-action="${step.action}">${esc(step.actionLabel)}</button>
          <label class="ge-preflight-manual" title="Mark this step done myself">
            <input type="checkbox" data-manual="${step.id}" ${manual ? 'checked' : ''}>
          </label>
        </div>`;
      const optTag = optional ? '<span class="ge-preflight-opt">optional</span>' : '';
      return `<div class="ge-preflight-row ${stateClass}" data-step="${step.id}">
        <span class="ge-preflight-badge" aria-hidden="true">${badge}</span>
        <div class="ge-preflight-body">
          <div class="ge-preflight-title">${esc(step.title)}${optTag}</div>
          <div class="ge-preflight-hint">${esc(step.hint)}</div>
          <div class="ge-preflight-detail">${esc(info.detail)}</div>
        </div>
        ${actions}
      </div>`;
    }).join('');

    el.innerHTML = `
      <div class="ge-preflight-card">
        <div class="ge-preflight-head">
          <div>
            <h3>Editor setup</h3>
            <p>A quick check before you start. Fix any ✕ below — optional steps can be skipped.</p>
          </div>
          <button type="button" class="ge-preflight-close" data-preflight-close aria-label="Close">×</button>
        </div>
        <div class="ge-preflight-rows">${rows}</div>
        <div class="ge-preflight-foot">
          <label class="ge-preflight-remember">
            <input type="checkbox" data-preflight-dontshow> Don’t show on open
          </label>
          <div class="ge-preflight-foot-actions">
            <span class="ge-preflight-summary">${doneRequired}/${requiredCount} required ready</span>
            <button type="button" class="ge-preflight-start" data-preflight-start>Start editing</button>
          </div>
        </div>
      </div>`;

    const dontShow = el.querySelector('[data-preflight-dontshow]');
    if (dontShow) {
      let dismissed = false;
      try { dismissed = localStorage.getItem(DISMISS_KEY) === '1'; } catch (_) {}
      dontShow.checked = dismissed;
    }
  }

  function open() {
    render();
    el.hidden = false;
  }
  function close() { el.hidden = true; }

  function maybeOpen() {
    let dismissed = false;
    try { dismissed = localStorage.getItem(DISMISS_KEY) === '1'; } catch (_) {}
    if (!dismissed) open();
  }

  const runAction = (name) => {
    if (name === 'retry') {
      window.dispatchEvent(new CustomEvent('ge:model-endpoints-updated'));
    } else if (name === 'model') {
      window.geEditorAiSettings?.open('ge-ai-command-model');
    } else if (name === 'agent') {
      window.geEditorAiSettings?.open('ge-ai-command-agent-model');
    }
    setTimeout(() => { if (!el.hidden) render(); }, 80);
  };

  el.addEventListener('click', (e) => {
    const act = e.target.closest('[data-action]');
    if (act) { e.preventDefault(); runAction(act.dataset.action); return; }
    if (e.target.closest('[data-preflight-close]') || e.target.closest('[data-preflight-start]')) {
      e.preventDefault();
      close();
      return;
    }
    // Click on the dimmed backdrop closes the checklist.
    if (e.target === el) close();
  });

  el.addEventListener('change', (e) => {
    const manual = e.target.closest('[data-manual]');
    if (manual) {
      const set = readOverride();
      if (manual.checked) set.add(manual.dataset.manual);
      else set.delete(manual.dataset.manual);
      writeOverride(set);
      render();
      return;
    }
    const dontShow = e.target.closest('[data-preflight-dontshow]');
    if (dontShow) {
      try {
        if (dontShow.checked) localStorage.setItem(DISMISS_KEY, '1');
        else localStorage.removeItem(DISMISS_KEY);
      } catch (_) {}
    }
  });

  // Re-render when model data changes or a model picker changes.
  const refresh = () => { if (!el.hidden) render(); };
  const onChange = (e) => {
    const sel = e.target.closest('#ge-ai-inpaint, #ge-ai-command-model, #ge-ai-command-agent-model');
    if (sel) refresh();
  };
  window.addEventListener('ge:ai-models-status', refresh);
  document.addEventListener('change', onChange);

  canvasArea.appendChild(el);

  function destroy() {
    window.removeEventListener('ge:ai-models-status', refresh);
    document.removeEventListener('change', onChange);
    el.remove();
  }

  return { element: el, open, close, maybeOpen, refresh, destroy };
}
