/**
 * AI model dropdown loader — fetches available model endpoints from
 * the backend and populates the editor's three model-select surfaces:
 *
 *   #ge-ai-model     — global Gen picker
 *   #ge-ai-inpaint   — inpaint picker
 *   select.ge-tool-model[data-ge-tool-model="…"]
 *                    — per-tool pickers (harmonize / upscale / style /
 *                      sharpen / etc.)
 *
 * Each model is filtered through a small capability classifier so the
 * Gen dropdown only sees text-to-image models, the inpaint dropdown
 * only sees image+mask edit models, and the per-tool dropdowns get
 * everything img2img-capable.
 *
 * Every picker ends with a "+ Serve a model in Cookbook…" sentinel —
 * choosing it opens Cookbook → Serve filtered to image models, then
 * reverts the picker to its prior value (so it's an action, not a
 * selectable model).
 *
 * @param {{
 *   container:              HTMLElement,
 *   apiBase:                string,
 *   openCookbookForImg2img: () => void,
 * }} deps
 */
import { state } from './state.js';
import { sortModelIds } from '../modelSort.js';

// Last result of loadAIModels() — consumed by the editor setup checklist
// (static/js/editor/preflight.js) to decide whether an image provider is
// reachable and which image models are available.
let _aiModelsStatus = { ready: false, models: [], error: null, at: 0 };
export function getAIModelsStatus() { return _aiModelsStatus; }

export function resolveInpaintModel(select) {
  const options = Array.from(select?.options || []).filter(option =>
    !option.disabled && option.value && option.value !== '__serve_cookbook__');
  const explicit = options.find(option => option.value === select.value);
  return explicit || options.find(option => /inpaint|edit|fill/i.test(option.value)) || options[0] || null;
}

// Resolve the model the Quick Edit box will use. Prefers the explicit Quick
// Edit picker (#ge-ai-command-model); falls back to the inpaint picker / Auto.
export function resolveQuickEditModel() {
  const sel = document.getElementById('ge-ai-command-model');
  if (sel && sel.value && sel.value !== '__serve_cookbook__') {
    const opt = sel.options[sel.selectedIndex];
    const raw = sel.value || '';
    const idx = raw.indexOf('::');
    return {
      endpoint: idx < 0 ? raw : raw.slice(0, idx),
      model: idx < 0 ? '' : raw.slice(idx + 2),
      label: ((opt && opt.textContent) || '').trim(),
    };
  }
  const generation = document.getElementById('ge-ai-model');
  if (generation?.value && generation.value !== '__serve_cookbook__') {
    const index = generation.value.indexOf('::');
    const model = index < 0 ? '' : generation.value.slice(index + 2);
    if (model && modelCaps(model, '', '').inpaint) {
      return { endpoint: generation.value.slice(0, index), model,
        label: generation.options[generation.selectedIndex]?.textContent?.trim() || model };
    }
  }
  return { endpoint: '', model: '', label: 'Auto (saved settings)' };
}

// Resolve the Quick Edit AGENT (planner) model — the vision-language model
// that reads the canvas screenshot and plans the edit. Default is the local
// Qwen3.5-9B VL server on seneca; "Server default" keeps the HARNESS planner.
export function resolveQuickEditAgentModel() {
  const sel = document.getElementById('ge-ai-command-agent-model');
  const raw = (sel && sel.value) || '';
  if (!raw || raw === 'default') {
    return { agent: '', endpoint: '', label: 'Server default' };
  }
  const idx = raw.indexOf('::');
  const endpoint = idx < 0 ? '' : raw.slice(0, idx);
  const agent = idx < 0 ? raw : raw.slice(idx + 2);
  const opt = sel.options[sel.selectedIndex];
  return { agent, endpoint, label: ((opt && opt.textContent) || '').trim() };
}

// Quick Edit agent (planner) model options. Kept small + explicit;
// dynamically populated from /api/editor/agent-models.
export const QUICK_EDIT_AGENT_OPTIONS = [
  { value: 'default', label: 'Server default (hosted planner)' },
];


// can see which model Quick Edit / inpaint will call.
export function updateFooterEditModel() {
  const el = document.getElementById('ge-status-model');
  if (!el) return;
  const info = resolveQuickEditModel();
  if (!info.label && !info.model) {
    el.textContent = 'Edit: none';
    el.title = 'No AI edit model available';
    return;
  }
  const label = info.label || info.model;
  el.textContent = `Edit: ${label}`;
  el.title = `AI edit model: ${label}`;
}

// Heuristic classifier on a model id + endpoint name. A model can be:
//   - gen: text-to-image generation
//   - inpaint: image+mask edit (inpaint / img2img)
// Some models do only one (e.g. dall-e-3 = gen-only, no edits API).
export function modelCaps(modelId, endpointName, endpointType) {
  const id = (modelId || '').toLowerCase();
  const name = (endpointName || '').toLowerCase();
  const type = (endpointType || '').toLowerCase();
  // Reject anything obviously text-only.
  const textOnly = /(?:^|[/\-_:])(gpt-?[345]|gpt-oss|claude|llama|qwen[^-]*chat|chat$|instruct$|coder)/i;
  if (textOnly.test(id) && !/image/i.test(id)) return { gen: false, inpaint: false };
  // OpenAI image family.
  if (/dall-e-3/.test(id))    return { gen: true,  inpaint: false };
  if (/dall-e-2/.test(id))    return { gen: true,  inpaint: true  };
  if (/(?:^|\/)(?:gpt-image[^/]*|gpt-[^/]*-image[^/]*|gemini-[^/]*image[^/]*|qwen-image[^/]*)$/.test(id)) {
    return { gen: true, inpaint: true };
  }
  // Diffusion families — most generic SD/SDXL/Flux base models
  // support both via diffusers.
  if (/(?:^|[/\-_])(?:sd-?xl|sdxl|sd3|sd-|stable[\s-]*diffusion|flux|playground|pixart|kandinsky)/i.test(id)) {
    const isInpaintModel = /inpaint|edit|fill/i.test(id) || /inpaint|edit|fill/i.test(name);
    return { gen: !isInpaintModel || /base/i.test(id), inpaint: true };
  }
  // Self-hosted diffusion server: model id often matches the repo
  // name; trust the endpoint name hint.
  if (type === 'image') {
    if (/inpaint|edit|fill/i.test(name)) return { gen: false, inpaint: true };
    return { gen: true, inpaint: true };
  }
  if (/inpaint|edit|fill/i.test(name)) return { gen: false, inpaint: true };
  if (/diffus|flux|sd|image/i.test(name)) return { gen: true, inpaint: true };
  // Editor image tools should be conservative. Unknown LLM/chat models
  // do not belong in image generation or inpaint pickers.
  return { gen: false, inpaint: false };
}

// Global listeners resolve live controls instead of retaining editor instances.
function syncCommandModel(value) {
  const select = document.getElementById('ge-ai-command-model');
  if (!select || !select._modelsReady) return;
  if (select.value !== value) {
    select.value = value;
    if (select.value !== value) select.value = '';
    updateFooterEditModel();
  }
}
if (typeof window !== 'undefined') {
  window.addEventListener('ge-ai-command-model-change', e => syncCommandModel(e.detail?.value || ''));
  window.addEventListener('storage', e => {
    if (e.key === 'ge-ai-command-model') syncCommandModel(e.newValue || '');
  });
}
globalThis.window?.addEventListener('ge:model-endpoints-updated', e => {
  document.getElementById('ge-ai-command-model')?._refreshAIModels?.(e.detail?.baseUrl || '');
});

export function wireAIModelSelectors({ container, apiBase = '', openCookbookForImg2img = () => {} } = {}) {
  const root = container || document;
  root._aiModelListeners?.abort();
  const listeners = new AbortController();
  root._aiModelListeners = listeners;
  const listen = (target, type, handler, options = {}) => target?.addEventListener(type, handler, {
    ...(typeof options === 'boolean' ? { capture: options } : options), signal: listeners.signal,
  });
  // Delegated handler for the "+ Serve a model in Cookbook…" sentinel
  // option — catches clicks regardless of whether loadAIModels has
  // rewired the individual select yet and survives any innerHTML
  // reset later.
  listen(root, 'change', (e) => {
    const sel = e.target.closest('select');
    if (!sel) return;
    if (sel.value !== '__serve_cookbook__') return;
    // Revert to the previous selection so the sentinel isn't "stuck".
    const prev = sel._prevServeValue ?? '';
    sel.value = prev;
    openCookbookForImg2img();
  });
  // Track prior value so we can restore it after the sentinel fires.
  listen(root, 'focus', (e) => {
    const sel = e.target.closest('select');
    if (sel && sel.value !== '__serve_cookbook__') sel._prevServeValue = sel.value;
  }, true);

  const aiGenSelect = document.getElementById('ge-ai-model');
  const aiInpaintSelect = document.getElementById('ge-ai-inpaint');
  const aiCommandSelect = document.getElementById('ge-ai-command-model');
  const aiCommandAgentSelect = document.getElementById('ge-ai-command-agent-model');
  if (aiInpaintSelect) listen(aiInpaintSelect, 'change', () => updateFooterEditModel());
  // The Quick Edit agent (planner) picker: dynamically populates from
  // /api/editor/agent-models; restore/save choice from localStorage.
  const populateAgentSelect = async () => {
    if (!aiCommandAgentSelect) return;
    const prevVal = aiCommandAgentSelect.value;
    let agentOptions = [...QUICK_EDIT_AGENT_OPTIONS];
    try {
      const res = await fetch(`${apiBase}/api/editor/agent-models`);
      if (res.ok) {
        const data = await res.json();
        if (listeners.signal.aborted || !root.isConnected) return;
        const serverModels = data.models || [];
        for (const m of serverModels) {
          const optVal = `${m.source === 'local' ? 'local' : (m.endpoint_id ? String(m.endpoint_id) : '')}::${m.id}`;
          const label = m.label || m.id;
          if (!agentOptions.some(o => o.value === optVal)) {
            agentOptions.push({ value: optVal, label });
          }
        }
      }
    } catch (_) {}

    aiCommandAgentSelect.innerHTML = '';
    for (const opt of agentOptions) {
      const el = document.createElement('option');
      el.value = opt.value;
      el.textContent = opt.label;
      aiCommandAgentSelect.appendChild(el);
    }
    try {
      const saved = localStorage.getItem('ge-ai-command-agent-model');
      if (saved && [...aiCommandAgentSelect.options].some(o => o.value === saved)) {
        aiCommandAgentSelect.value = saved;
      } else if (prevVal && [...aiCommandAgentSelect.options].some(o => o.value === prevVal)) {
        aiCommandAgentSelect.value = prevVal;
      }
    } catch {}
    if (aiCommandAgentSelect._agentWired !== listeners) {
      aiCommandAgentSelect._agentWired = listeners;
      listen(aiCommandAgentSelect, 'change', () => {
        try { localStorage.setItem('ge-ai-command-agent-model', aiCommandAgentSelect.value); } catch {}
      });
    }
  };
  // The global Gen model dropdown was removed from the editor topbar;
  // only bail if there's nothing to populate at all (neither the Gen
  // select nor the inpaint select nor any per-tool select).
  if (!aiGenSelect && !aiInpaintSelect && !aiCommandSelect && !aiCommandAgentSelect &&
      !document.querySelector('select.ge-tool-model')) return;
  void populateAgentSelect();

  async function loadAIModels(opts = {}) {
    try {
      const selectBaseUrl = opts.selectBaseUrl || '';
      const prevGenValue = aiGenSelect?.value || '';
      const prevInpaintValue = aiInpaintSelect?.value || '';
      const res = await fetch(`${apiBase}/api/editor/image-models`);
      if (listeners.signal.aborted || !root.isConnected) return;
      if (!res.ok) throw new Error(`Could not load image models (${res.status})`);
      const data = await res.json();
      if (listeners.signal.aborted || !root.isConnected) return;
      const modelsList = data.models || [];
      if (aiGenSelect) aiGenSelect.innerHTML = '<option value="">Auto-detect</option>';
      if (aiInpaintSelect) aiInpaintSelect.innerHTML = '<option value="">Auto</option>';
      if (aiCommandSelect) aiCommandSelect.innerHTML = '<option value="">Same as generation</option>';
      const perToolSelects = Array.from(document.querySelectorAll('select.ge-tool-model'));
      for (const ts of perToolSelects) ts.innerHTML = '<option value="">Auto</option>';
      let firstGen = null;
      let selectedGen = null;
      let selectedInpaint = null;
      const loaded = [];
      for (const m of modelsList) {
        const modelId = m.id || '';
        const caps = modelCaps(modelId, m.provider, m.kind);
        const canGen = Boolean(caps.gen || m.kind === 'generate');
        const canInpaint = Boolean(caps.inpaint || m.kind === 'edit');
        if (!canGen && !canInpaint) continue;

        // Use opaque endpoint_id if available; never fall back to raw base_url
        const endpointKey = m.endpoint_id ? String(m.endpoint_id) : (m.source === 'local' ? 'local' : '');
        const value = endpointKey ? `${endpointKey}::${modelId}` : modelId;
        const shortModel = modelId ? String(modelId).split('/').pop() : (m.label || m.provider);
        const epHint = m.provider && m.provider !== modelId && m.provider !== 'image' ? ` · ${m.provider}` : '';
        const label = `${shortModel}${epHint}`;
        const epUsable = true;
        loaded.push({ value, label, gen: canGen, inpaint: canInpaint, usable: epUsable, endpoint: endpointKey, model: modelId });
        if (canGen && aiGenSelect) {
          const opt = document.createElement('option');
          opt.value = value;
          opt.textContent = label;
          opt.disabled = !epUsable;
          aiGenSelect.appendChild(opt);
          if (epUsable && !firstGen) firstGen = value;
          if (epUsable && selectBaseUrl && (endpointKey === selectBaseUrl || m.base_url === selectBaseUrl) && !selectedGen) selectedGen = value;
        }
        if (canInpaint && aiInpaintSelect) {
          const opt = document.createElement('option');
          opt.value = value;
          opt.textContent = label;
          opt.disabled = !epUsable;
          aiInpaintSelect.appendChild(opt);
          if (epUsable && selectBaseUrl && (endpointKey === selectBaseUrl || m.base_url === selectBaseUrl) && !selectedInpaint) selectedInpaint = value;
        }
        if (canInpaint || canGen) {
          for (const ts of perToolSelects) {
            const opt = document.createElement('option');
            opt.value = value;
            opt.textContent = label;
            opt.disabled = !epUsable;
            ts.appendChild(opt);
          }
          if (aiCommandSelect) {
            const opt = document.createElement('option');
            opt.value = value;
            opt.textContent = label;
            opt.disabled = !epUsable;
            aiCommandSelect.appendChild(opt);
          }
        }
      }
      const hasValue = (sel, value) => !!value && [...sel.options].some(o => o.value === value);
      if (aiGenSelect) {
        if (selectedGen) aiGenSelect.value = selectedGen;
        else if (hasValue(aiGenSelect, prevGenValue)) aiGenSelect.value = prevGenValue;
        else if (firstGen) aiGenSelect.value = firstGen;
      }
      if (aiInpaintSelect) {
        if (selectedInpaint) aiInpaintSelect.value = selectedInpaint;
        else if (hasValue(aiInpaintSelect, prevInpaintValue)) aiInpaintSelect.value = prevInpaintValue;
        const auto = resolveInpaintModel({ options: aiInpaintSelect.options, value: '' });
        aiInpaintSelect.options[0].textContent = auto ? `Auto (${auto.textContent})` : 'Auto (no available model)';
      }
      updateFooterEditModel();
      // Append the "Serve a model in Cookbook…" sentinel at the
      // bottom of every model dropdown.
      const appendServeSentinel = (sel) => {
        const sep = document.createElement('option');
        sep.disabled = true;
        sep.textContent = '──────────';
        sel.appendChild(sep);
        const serveOpt = document.createElement('option');
        serveOpt.value = '__serve_cookbook__';
        serveOpt.textContent = '+ Serve a model in Cookbook…';
        sel.appendChild(serveOpt);
      };
      for (const ts of perToolSelects) appendServeSentinel(ts);
      if (aiGenSelect) appendServeSentinel(aiGenSelect);
      if (aiInpaintSelect) appendServeSentinel(aiInpaintSelect);
      if (aiCommandSelect) appendServeSentinel(aiCommandSelect);
      // Sentinel changes are handled by the component's delegated listener.
      if (aiCommandSelect) {
        aiCommandSelect._modelsReady = true;
        // Legacy URL migration check: legacy endpoints with raw URLs are scrubbed.
        const scrubModelUrl = (saved) => (saved && saved.startsWith('http://') ? '' : saved);
        if (aiCommandSelect._commandWired !== listeners) {
          aiCommandSelect._commandWired = listeners;
          listen(aiCommandSelect, 'change', () => {
            if (aiCommandSelect.value === '__serve_cookbook__') return;
            const val = scrubModelUrl(aiCommandSelect.value) || '';
            updateFooterEditModel();
            globalThis.window?.dispatchEvent?.(new CustomEvent('ge-ai-command-model-change', { detail: { value: val } }));
          });
        }
      }
      // Restore each per-tool selection from localStorage.
      for (const ts of perToolSelects) {
        const key = 'ge-tool-model-' + ts.dataset.geToolModel;
        try {
          const saved = localStorage.getItem(key);
          if (saved && [...ts.options].some(o => o.value === saved)) {
            ts.value = saved;
          }
        } catch {}
        let prevValue = ts.value;
        listen(ts, 'change', () => {
          if (ts.value === '__serve_cookbook__') {
            ts.value = prevValue;
            openCookbookForImg2img();
            return;
          }
          prevValue = ts.value;
          try { localStorage.setItem(key, ts.value); } catch {}
        });
      }
      _aiModelsStatus = { ready: true, models: loaded, error: null, at: Date.now() };
      container.dispatchEvent(new Event('ge-image-models-ready'));
      try { window.dispatchEvent(new CustomEvent('ge:ai-models-status')); } catch {}
    } catch (e) {
      if (listeners.signal.aborted || !root.isConnected) return;
      // Fetch failed — still give the user the affordance to set up
      // a model. Otherwise the dropdown shows only "Auto" with no
      // hint about what to do next.
      const fallback = '<option value="">Auto</option><option disabled>Could not load image models</option><option value="__serve_cookbook__">+ Serve a model in Cookbook…</option>';
      if (aiGenSelect) aiGenSelect.innerHTML = fallback;
      if (aiInpaintSelect) aiInpaintSelect.innerHTML = fallback;
      if (aiCommandSelect) aiCommandSelect.innerHTML = fallback;
      document.querySelectorAll('select.ge-tool-model').forEach(ts => { ts.innerHTML = fallback; });
      void populateAgentSelect();
      updateFooterEditModel();
      _aiModelsStatus = { ready: false, models: [], error: String(e?.message || e), at: Date.now() };
      try { window.dispatchEvent(new CustomEvent('ge:ai-models-status')); } catch {}
    }
  }
  loadAIModels();
  if (aiCommandSelect) aiCommandSelect._refreshAIModels = baseUrl => {
    if (listeners.signal.aborted || !root.isConnected) return;
    void loadAIModels({ selectBaseUrl: baseUrl });
    void populateAgentSelect();
  };
  // Re-fetch the model list when the user opens the inpaint dropdown,
  // so a model served via Cookbook mid-edit shows up without having to
  // close and reopen the editor. Debounced to one refresh per 3s so
  // rapid open/close doesn't hammer the endpoint. Preserves the
  // current selection across the reload.
  let _lastModelRefresh = 0;
  const refreshOnOpen = (e) => {
    const sel = e.target.closest('#ge-ai-inpaint, select.ge-tool-model');
    if (!sel) return;
    const now = Date.now();
    if (now - _lastModelRefresh < 3000) return;
    _lastModelRefresh = now;
    const keep = sel.value;
    loadAIModels().then(() => {
      // Restore the prior selection if it still exists.
      if ([...sel.options].some(o => o.value === keep)) sel.value = keep;
    });
  };
  listen(root, 'mousedown', refreshOnOpen, true);
}
