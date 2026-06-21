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

const SERVE_IMAGE_MODEL_VALUE = '__serve_cookbook__';

function imageEditCueText(...parts) {
  return parts.map(part => String(part || '').toLowerCase()).join(' ');
}

function hasImageEditCue(text) {
  return /gpt-image|chatgpt-image|dall-e-2|kontext|inpaint|outpaint|img2img|image[-_\s]*to[-_\s]*image|image2image|i2i|edit|edits|fill|mask|masked|paint[-_\s]*by[-_\s]*example|pix2pix|instruct[-_\s]*pix2pix|variation|variations/i.test(text || '');
}

function hasEndpointInpaintSurfaceCue(text) {
  return /diffusion|stable[-_\s]*diffusion|sdxl|sd[-_\s]*webui|automatic1111|a1111|forge|comfy|fooocus|invoke|swarm|inpaint|img2img|image[-_\s]*edit|local[-/_\s]*api|flux|kontext/i.test(text || '');
}

function hasModernImageEditCue(modelId, endpointName = '') {
  const id = (modelId || '').toLowerCase();
  const text = imageEditCueText(id, endpointName);
  if (/dall-e-3/.test(id)) return false;
  return /gpt-image|chatgpt-image|dall-e-2|kontext/i.test(text)
    || hasImageEditCue(text)
    || (/qwen/i.test(text) && /image/i.test(text) && /edit|inpaint|fill|mask/i.test(text))
    || (/seedream/i.test(text) && /edit|inpaint|fill|mask/i.test(text));
}

function isPreferredInpaintModel(modelId, endpointName = '', endpoint = {}) {
  const id = (modelId || '').toLowerCase();
  const name = imageEditCueText(endpointName, endpoint?.base_url, endpoint?.endpoint_kind, endpoint?.category);
  return !modelId
    || hasModernImageEditCue(id, name)
    || hasImageEditCue(id)
    || hasEndpointInpaintSurfaceCue(name)
    || /rembg|remove-?bg|background[-\s]*remove/i.test(name);
}

// Heuristic classifier on a model id + endpoint name. A model can be:
//   - gen: text-to-image generation
//   - inpaint: image+mask edit (inpaint / img2img)
// Some models do only one (e.g. dall-e-3 = gen-only, no edits API).
function modelCaps(modelId, endpointName, endpointType, endpoint = {}) {
  const id = (modelId || '').toLowerCase();
  const name = (endpointName || '').toLowerCase();
  const type = (endpointType || '').toLowerCase();
  const endpointText = imageEditCueText(
    endpointName,
    endpoint?.base_url,
    endpoint?.endpoint_kind,
    endpoint?.category,
  );
  const combined = imageEditCueText(id, endpointText);
  const editCue = hasImageEditCue(combined);
  const endpointCanSurfaceInpaint = type === 'image' && hasEndpointInpaintSurfaceCue(endpointText);
  // Reject anything obviously text-only.
  const textOnly = /(?:^|[/\-_:])(gpt-?[345]|gpt-oss|claude|llama|qwen[^-]*chat|chat$|instruct$|coder)/i;
  if (textOnly.test(id) && !/image|vision|edit|inpaint|fill|kontext/i.test(id)) return { gen: false, inpaint: false };
  // OpenAI image family.
  if (/dall-e-3/.test(id))    return { gen: true,  inpaint: false };
  if (/dall-e-2/.test(id))    return { gen: true,  inpaint: true  };
  if (/gpt-image/.test(id))   return { gen: true,  inpaint: true  };
  if (hasModernImageEditCue(id, name)) return { gen: true, inpaint: true };
  // Diffusion families: base models are generation-only unless the
  // model/endpoint explicitly advertises an edit, fill, or inpaint path.
  if (/(?:^|[/\-_])(?:sd-?xl|sdxl|sd3|sd-|stable[\s-]*diffusion|flux|playground|pixart|kandinsky|controlnet)/i.test(id)) {
    const isInpaintModel = editCue || endpointCanSurfaceInpaint;
    return { gen: !isInpaintModel || /base/i.test(id), inpaint: isInpaintModel };
  }
  // Self-hosted diffusion server: model id often matches the repo
  // name; trust the endpoint name hint.
  if (type === 'image') {
    if (!String(modelId || '').trim()) return { gen: !endpointCanSurfaceInpaint, inpaint: endpointCanSurfaceInpaint || editCue };
    if (editCue || endpointCanSurfaceInpaint) return { gen: !/inpaint|fill|mask|rembg|remove-?bg|background[-\s]*remove/i.test(combined), inpaint: true };
    return { gen: true, inpaint: false };
  }
  if (editCue || hasEndpointInpaintSurfaceCue(name)) return { gen: false, inpaint: true };
  if (/diffus|flux|sd|image/i.test(name)) return { gen: true, inpaint: false };
  // Editor image tools should be conservative. Unknown LLM/chat models
  // do not belong in image generation or inpaint pickers.
  return { gen: false, inpaint: false };
}

function pickerHostFromValue(value) {
  if (!value) return '';
  const endpoint = value.includes('::') ? value.slice(0, value.indexOf('::')) : value;
  try { return new URL(endpoint).host; } catch { return endpoint.replace(/^https?:\/\//i, ''); }
}

function inpaintModelCount(select) {
  if (!select) return 0;
  return Array.from(select.options).filter(opt =>
    !isInpaintPickerSeparatorOption(opt) &&
    opt.value &&
    opt.value !== SERVE_IMAGE_MODEL_VALUE &&
    !opt.disabled
  ).length;
}

function isInpaintPickerSeparatorOption(opt) {
  const value = opt?.value || '';
  const text = (opt?.textContent || '').trim();
  return !value && (opt?.disabled || /^[\s─-]+$/.test(text));
}

function describeInpaintOption(opt, select) {
  const value = opt?.value || '';
  const text = (opt?.textContent || '').trim();
  if (!value) {
    const count = inpaintModelCount(select);
    return {
      label: 'Auto',
      meta: count ? `${count} image-edit model${count === 1 ? '' : 's'} available` : 'No image-edit endpoints found. LM Studio/GGUF downloads need a Diffusers or ONNX image endpoint.',
      kind: 'auto',
    };
  }
  if (value === SERVE_IMAGE_MODEL_VALUE) {
    return {
      label: 'Serve image model',
      meta: 'Open Cookbook to add or start a Diffusers/ONNX inpaint-capable endpoint',
      kind: 'action',
    };
  }
  const offline = /\(offline\)$/i.test(text) || !!opt?.disabled;
  const cleanText = text.replace(/\s+\(offline\)$/i, '');
  const idx = value.indexOf('::');
  const modelId = idx >= 0 ? value.slice(idx + 2) : '';
  const fallback = modelId ? modelId.split('/').pop() : 'Image edit model';
  const parts = /^[\s─-]+$/.test(cleanText)
    ? []
    : cleanText.split(' · ').map(p => p.trim()).filter(Boolean);
  const label = parts.shift() || fallback;
  const host = pickerHostFromValue(value);
  const meta = [
    ...parts,
    host,
    offline ? 'offline' : 'ready',
  ].filter(Boolean).join(' · ');
  return {
    label,
    meta,
    kind: offline ? 'offline' : 'model',
    disabled: !!opt?.disabled,
  };
}

function pickerOptions(select) {
  if (!select) return [];
  return Array.from(select.options).filter(opt =>
    !isInpaintPickerSeparatorOption(opt) &&
    (opt.value || !opt.disabled)
  );
}

function wireInpaintModelPicker({ select, container, openCookbookForImg2img, refreshModels }) {
  const row = select?.closest('.ge-inpaint-model-row');
  if (!row || row.querySelector('.ge-inpaint-model-picker')) return null;

  select.classList.add('ge-model-native-select');
  select.setAttribute('aria-hidden', 'true');
  select.tabIndex = -1;
  document.getElementById('ge-ai-inpaint-picker-menu')?.remove();

  const wrap = document.createElement('div');
  wrap.className = 'ge-inpaint-model-picker';

  const btn = document.createElement('button');
  btn.type = 'button';
  btn.id = 'ge-ai-inpaint-picker-btn';
  btn.className = 'ge-inpaint-model-picker-btn';
  btn.setAttribute('aria-haspopup', 'dialog');
  btn.setAttribute('aria-expanded', 'false');

  const label = document.createElement('span');
  label.className = 'ge-inpaint-model-picker-label';
  label.textContent = 'Auto';

  const meta = document.createElement('span');
  meta.className = 'ge-inpaint-model-picker-meta';

  const chevron = document.createElement('span');
  chevron.className = 'ge-inpaint-model-picker-chevron';
  chevron.setAttribute('aria-hidden', 'true');
  chevron.textContent = 'v';

  btn.append(label, meta, chevron);

  const menu = document.createElement('div');
  menu.id = 'ge-ai-inpaint-picker-menu';
  menu.className = 'ge-inpaint-model-picker-menu';
  menu.hidden = true;
  menu.setAttribute('role', 'dialog');
  menu.setAttribute('aria-label', 'Choose inpaint model');

  const head = document.createElement('div');
  head.className = 'ge-inpaint-model-picker-head';
  const title = document.createElement('strong');
  title.textContent = 'Inpaint model';
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.className = 'ge-inpaint-model-picker-close';
  closeBtn.setAttribute('aria-label', 'Close model picker');
  closeBtn.textContent = 'x';
  head.append(title, closeBtn);

  const search = document.createElement('input');
  search.id = 'ge-ai-inpaint-picker-search';
  search.className = 'ge-inpaint-model-picker-search';
  search.type = 'search';
  search.autocomplete = 'off';
  search.placeholder = 'Search image edit models...';

  const list = document.createElement('div');
  list.id = 'ge-ai-inpaint-picker-list';
  list.className = 'ge-inpaint-model-picker-list';

  menu.append(head, search, list);
  wrap.append(btn);
  select.insertAdjacentElement('afterend', wrap);
  document.body.appendChild(menu);

  let refreshInFlight = null;

  function setButtonFromSelection() {
    const selected = select.selectedOptions?.[0] || pickerOptions(select)[0] || null;
    const desc = describeInpaintOption(selected, select);
    label.textContent = desc.label;
    meta.textContent = desc.meta || 'Tap to choose the inpaint/edit endpoint';
    btn.classList.toggle('is-auto', !select.value);
    btn.classList.toggle('is-offline', desc.kind === 'offline');
    btn.title = desc.meta ? `${desc.label} - ${desc.meta}` : desc.label;
  }

  function focusActive() {
    const active = list.querySelector('.ge-inpaint-model-option.is-selected:not(:disabled)')
      || list.querySelector('.ge-inpaint-model-option:not(:disabled)');
    if (active) active.classList.add('kb-active');
  }

  function moveActive(dir) {
    const items = Array.from(list.querySelectorAll('.ge-inpaint-model-option:not(:disabled)'));
    if (!items.length) return;
    const cur = items.findIndex(el => el.classList.contains('kb-active'));
    items.forEach(el => el.classList.remove('kb-active'));
    const next = cur < 0 ? 0 : (cur + dir + items.length) % items.length;
    items[next].classList.add('kb-active');
    items[next].scrollIntoView({ block: 'nearest' });
  }

  function chooseOption(opt) {
    if (!opt || opt.disabled) return;
    if (opt.value === SERVE_IMAGE_MODEL_VALUE) {
      close();
      openCookbookForImg2img();
      return;
    }
    select.value = opt.value;
    select._prevServeValue = opt.value;
    select.dispatchEvent(new Event('change', { bubbles: true }));
    setButtonFromSelection();
    close();
  }

  function addSection(text) {
    const section = document.createElement('div');
    section.className = 'ge-inpaint-model-picker-section';
    section.textContent = text;
    list.appendChild(section);
  }

  function appendOption(opt) {
    const desc = describeInpaintOption(opt, select);
    const item = document.createElement('button');
    item.type = 'button';
    item.className = `ge-inpaint-model-option ge-inpaint-model-option-${desc.kind}`;
    item.disabled = !!desc.disabled;
    item.classList.toggle('is-selected', opt.value === select.value);
    const itemLabel = document.createElement('span');
    itemLabel.className = 'ge-inpaint-model-option-label';
    itemLabel.textContent = desc.label;
    const itemMeta = document.createElement('span');
    itemMeta.className = 'ge-inpaint-model-option-meta';
    itemMeta.textContent = desc.meta;
    item.append(itemLabel, itemMeta);
    item.addEventListener('click', () => chooseOption(opt));
    list.appendChild(item);
  }

  function renderList(query = '') {
    const q = query.trim().toLowerCase();
    list.innerHTML = '';
    const opts = pickerOptions(select);
    const autoOpts = opts.filter(opt => !opt.value);
    const modelOpts = opts.filter(opt => opt.value && opt.value !== SERVE_IMAGE_MODEL_VALUE);
    const serveOpts = opts.filter(opt => opt.value === SERVE_IMAGE_MODEL_VALUE);
    const matches = (opt) => {
      if (!q) return true;
      const desc = describeInpaintOption(opt, select);
      return `${desc.label} ${desc.meta} ${opt.value}`.toLowerCase().includes(q);
    };
    const renderGroup = (section, group) => {
      const filtered = group.filter(matches);
      if (!filtered.length) return;
      if (!q) addSection(section);
      filtered.forEach(appendOption);
    };
    renderGroup('Default', autoOpts);
    renderGroup('Available models', modelOpts);
    renderGroup('Setup', serveOpts);
    if (!list.children.length) {
      const empty = document.createElement('div');
      empty.className = 'ge-inpaint-model-picker-empty';
      empty.textContent = 'No matching image edit models. LM Studio GGUF models need a Diffusers or ONNX image endpoint for inpaint.';
      list.appendChild(empty);
    }
    focusActive();
  }

  function visiblePickerBounds(pad = 8) {
    const viewport = {
      left: pad,
      top: pad,
      right: Math.max(pad, (window.innerWidth || document.documentElement.clientWidth || 0) - pad),
      bottom: Math.max(pad, (window.innerHeight || document.documentElement.clientHeight || 0) - pad),
    };
    const surface = container?.closest?.('.gallery-editor')
      || container?.closest?.('.gallery-modal-content, .modal-content')
      || container;
    const rect = surface?.getBoundingClientRect?.();
    if (!rect || rect.width < 80 || rect.height < 80) return viewport;
    const bounds = {
      left: Math.max(viewport.left, rect.left + pad),
      top: Math.max(viewport.top, rect.top + pad),
      right: Math.min(viewport.right, rect.right - pad),
      bottom: Math.min(viewport.bottom, rect.bottom - pad),
    };
    if (bounds.right - bounds.left < 160 || bounds.bottom - bounds.top < 120) return viewport;
    return bounds;
  }

  function placeMenu() {
    if (menu.hidden) return;
    const rect = btn.getBoundingClientRect();
    const vw = window.innerWidth || document.documentElement.clientWidth || 0;
    if (vw <= 700) {
      menu.style.left = '';
      menu.style.right = '';
      menu.style.top = '';
      menu.style.bottom = '';
      menu.style.width = '';
      menu.style.minWidth = '';
      menu.style.maxWidth = '';
      menu.style.maxHeight = '';
      return;
    }
    const bounds = visiblePickerBounds(8);
    const availableWidth = Math.max(180, bounds.right - bounds.left);
    const width = Math.min(Math.max(rect.width, 320), availableWidth);
    let left = rect.left;
    if (left + width > bounds.right) left = bounds.right - width;
    if (left < bounds.left) left = bounds.left;
    const below = bounds.bottom - rect.bottom;
    const above = rect.top - bounds.top;
    const flipUp = below < 260 && above > below;
    menu.style.left = `${Math.round(left)}px`;
    menu.style.right = 'auto';
    menu.style.width = `${Math.round(width)}px`;
    menu.style.minWidth = `${Math.round(Math.min(260, width))}px`;
    menu.style.maxWidth = `${Math.round(availableWidth)}px`;
    if (flipUp) {
      menu.style.top = 'auto';
      menu.style.bottom = `${Math.round((window.innerHeight || document.documentElement.clientHeight || bounds.bottom) - Math.min(rect.top, bounds.bottom) + 6)}px`;
      menu.style.maxHeight = `${Math.max(80, Math.min(420, above - 8))}px`;
    } else {
      menu.style.bottom = 'auto';
      menu.style.top = `${Math.round(Math.max(bounds.top, rect.bottom + 6))}px`;
      menu.style.maxHeight = `${Math.max(80, Math.min(420, below - 8))}px`;
    }
  }

  function close() {
    if (menu.hidden) return;
    menu.hidden = true;
    btn.setAttribute('aria-expanded', 'false');
    search.value = '';
    window.removeEventListener('resize', placeMenu);
    window.removeEventListener('scroll', placeMenu, true);
  }

  function open() {
    menu.hidden = false;
    btn.setAttribute('aria-expanded', 'true');
    renderList('');
    placeMenu();
    window.addEventListener('resize', placeMenu);
    window.addEventListener('scroll', placeMenu, true);
    requestAnimationFrame(() => {
      const touchLike = window.matchMedia?.('(hover: none), (pointer: coarse)')?.matches;
      if (!touchLike && (window.innerWidth || 0) > 700) {
        search.focus({ preventScroll: true });
      }
      placeMenu();
    });
    if (!refreshInFlight && typeof refreshModels === 'function') {
      menu.classList.add('is-loading');
      refreshInFlight = Promise.resolve(refreshModels())
        .catch(() => {})
        .finally(() => {
          refreshInFlight = null;
          menu.classList.remove('is-loading');
          setButtonFromSelection();
          renderList(search.value);
          placeMenu();
        });
    }
  }

  btn.addEventListener('click', () => {
    if (menu.hidden) open();
    else close();
  });
  closeBtn.addEventListener('click', close);
  search.addEventListener('input', () => renderList(search.value));
  search.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') { e.preventDefault(); close(); return; }
    if (e.key === 'ArrowDown') { e.preventDefault(); moveActive(1); return; }
    if (e.key === 'ArrowUp') { e.preventDefault(); moveActive(-1); return; }
    if (e.key === 'Enter') {
      e.preventDefault();
      const active = list.querySelector('.ge-inpaint-model-option.kb-active:not(:disabled)')
        || list.querySelector('.ge-inpaint-model-option:not(:disabled)');
      if (active) active.click();
    }
  });
  list.addEventListener('mousemove', (e) => {
    const item = e.target.closest('.ge-inpaint-model-option');
    if (!item || item.disabled) return;
    list.querySelectorAll('.kb-active').forEach(el => el.classList.remove('kb-active'));
    item.classList.add('kb-active');
  });
  const onPointerOutside = (e) => {
    if (!container.isConnected) {
      close();
      menu.remove();
      document.removeEventListener('pointerdown', onPointerOutside, true);
      return;
    }
    if (menu.hidden) return;
    if (wrap.contains(e.target) || menu.contains(e.target)) return;
    close();
  };
  document.addEventListener('pointerdown', onPointerOutside, true);
  select.addEventListener('change', setButtonFromSelection);
  setButtonFromSelection();

  return {
    sync() {
      setButtonFromSelection();
      if (!menu.hidden) {
        renderList(search.value);
        placeMenu();
      }
    },
    close,
  };
}

export function wireAIModelSelectors({ container, apiBase, openCookbookForImg2img }) {
  // Delegated handler for the "+ Serve a model in Cookbook…" sentinel
  // option — catches clicks regardless of whether loadAIModels has
  // rewired the individual select yet and survives any innerHTML
  // reset later.
  container.addEventListener('change', (e) => {
    const sel = e.target.closest('select');
    if (!sel) return;
    if (sel.value !== SERVE_IMAGE_MODEL_VALUE) return;
    // Revert to the previous selection so the sentinel isn't "stuck".
    const prev = sel._prevServeValue ?? '';
    sel.value = prev;
    openCookbookForImg2img();
  });
  // Track prior value so we can restore it after the sentinel fires.
  container.addEventListener('focus', (e) => {
    const sel = e.target.closest('select');
    if (sel && sel.value !== SERVE_IMAGE_MODEL_VALUE) sel._prevServeValue = sel.value;
  }, true);

  const aiGenSelect = document.getElementById('ge-ai-model');
  const aiInpaintSelect = document.getElementById('ge-ai-inpaint');
  let _lastModelRefresh = 0;
  const refreshInpaintModels = () => {
    const now = Date.now();
    if (now - _lastModelRefresh < 3000) return Promise.resolve();
    _lastModelRefresh = now;
    const keep = aiInpaintSelect?.value || '';
    return loadAIModels().then(() => {
      if (aiInpaintSelect && keep && [...aiInpaintSelect.options].some(o => o.value === keep)) {
        aiInpaintSelect.value = keep;
      }
      inpaintPicker?.sync();
    });
  };
  const inpaintPicker = aiInpaintSelect
    ? wireInpaintModelPicker({
        select: aiInpaintSelect,
        container,
        openCookbookForImg2img,
        refreshModels: refreshInpaintModels,
      })
    : null;
  // The global Gen model dropdown was removed from the editor topbar;
  // only bail if there's nothing to populate at all (neither the Gen
  // select nor the inpaint select nor any per-tool select).
  if (!aiGenSelect && !aiInpaintSelect &&
      !document.querySelector('select.ge-tool-model')) return;

  async function loadAIModels(opts = {}) {
    try {
      const selectBaseUrl = opts.selectBaseUrl || '';
      const prevGenValue = aiGenSelect?.value || '';
      const prevInpaintValue = aiInpaintSelect?.value || '';
      const res = await fetch(`${apiBase}/api/model-endpoints`);
      const endpoints = await res.json();
      if (aiGenSelect) aiGenSelect.innerHTML = '<option value="">None</option>';
      if (aiInpaintSelect) aiInpaintSelect.innerHTML = '<option value="">Auto</option>';
      const perToolSelects = Array.from(document.querySelectorAll('select.ge-tool-model'));
      for (const ts of perToolSelects) ts.innerHTML = '<option value="">Auto</option>';
      let firstGen = null;
      let firstInpaint = null;
      let selectedGen = null;
      let selectedInpaint = null;
      for (const ep of endpoints) {
        if (!ep.is_enabled) continue;
        const hasListedModels = Array.isArray(ep.models) && ep.models.length;
        const models = hasListedModels ? sortModelIds(ep.models) : [''];
        const isImageEndpoint = (ep.model_type || '').toLowerCase() === 'image';
        // Image/inpaint endpoints can be called by URL even when their
        // /models cache is still empty, so don't strand a freshly served
        // Cookbook model as "(offline)" in the editor picker.
        const epUsable = !!ep.online || isImageEndpoint;
        for (const modelId of models) {
          const caps = modelCaps(modelId, ep.name, ep.model_type, ep);
          if (!caps.gen && !caps.inpaint) continue;
          // Encode "<base_url>::<model_id>" so the value carries both pieces.
          const value = `${ep.base_url}::${modelId}`;
          const shortModel = modelId ? String(modelId).split('/').pop() : (ep.name || ep.base_url);
          const epHint = modelId && ep.name && ep.name !== modelId ? ` · ${ep.name}` : '';
          const label = `${shortModel}${epHint}${epUsable ? '' : ' (offline)'}`;
          if (caps.gen && aiGenSelect) {
            const opt = document.createElement('option');
            opt.value = value;
            opt.textContent = label;
            opt.disabled = !epUsable;
            aiGenSelect.appendChild(opt);
            if (epUsable && !firstGen) firstGen = value;
            if (epUsable && selectBaseUrl && ep.base_url === selectBaseUrl && !selectedGen) selectedGen = value;
          }
          if (caps.inpaint && aiInpaintSelect) {
            const opt = document.createElement('option');
            opt.value = value;
            opt.textContent = label;
            opt.disabled = !epUsable;
            aiInpaintSelect.appendChild(opt);
            if (epUsable && selectBaseUrl && ep.base_url === selectBaseUrl && !selectedInpaint) selectedInpaint = value;
            // Prefer dedicated inpaint/edit models for default selection.
            if (epUsable && !firstInpaint && isPreferredInpaintModel(modelId, ep.name || '', ep)) {
              firstInpaint = value;
            }
          }
          // Per-tool selectors get every img2img-capable entry. Both
          // caps.inpaint AND caps.gen models work for harmonize /
          // style / upscale (anything that can do img2img).
          if (caps.inpaint || caps.gen) {
            for (const ts of perToolSelects) {
              if (ts.dataset.geToolModel === 'rembg' && !caps.inpaint) continue;
              const opt = document.createElement('option');
              opt.value = value;
              opt.textContent = label;
              opt.disabled = !epUsable;
              ts.appendChild(opt);
            }
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
        else if (firstInpaint) aiInpaintSelect.value = firstInpaint;
      }
      // Append the "Serve a model in Cookbook…" sentinel at the
      // bottom of every model dropdown.
      const appendLocalRembgOptions = (sel) => {
        if (!sel || sel.dataset.geToolModel !== 'rembg') return;
        const sep = document.createElement('option');
        sep.disabled = true;
        sep.textContent = '── Local rembg ──';
        sel.appendChild(sep);
        [
          ['::isnet-general-use', 'ISNet general use · best local'],
          ['::silueta', 'Silueta · balanced local'],
          ['::u2netp', 'u2netp · fast fallback'],
        ].forEach(([value, label]) => {
          const opt = document.createElement('option');
          opt.value = value;
          opt.textContent = label;
          sel.appendChild(opt);
        });
      };
      for (const ts of perToolSelects) appendLocalRembgOptions(ts);
      const appendServeSentinel = (sel) => {
        const sep = document.createElement('option');
        sep.disabled = true;
        sep.textContent = '──────────';
        sel.appendChild(sep);
        const serveOpt = document.createElement('option');
        serveOpt.value = SERVE_IMAGE_MODEL_VALUE;
        serveOpt.textContent = '+ Serve a model in Cookbook…';
        sel.appendChild(serveOpt);
      };
      for (const ts of perToolSelects) appendServeSentinel(ts);
      if (aiGenSelect) appendServeSentinel(aiGenSelect);
      if (aiInpaintSelect) appendServeSentinel(aiInpaintSelect);
      // Wire the sentinel on the Gen + Inpaint selects too.
      const wireServeSentinel = (sel) => {
        if (!sel) return;
        let prev = sel.value;
        sel.addEventListener('change', () => {
          if (sel.value === SERVE_IMAGE_MODEL_VALUE) {
            sel.value = prev;
            openCookbookForImg2img();
            return;
          }
          prev = sel.value;
        });
      };
      wireServeSentinel(aiGenSelect);
      wireServeSentinel(aiInpaintSelect);
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
        ts.addEventListener('change', () => {
          if (ts.value === SERVE_IMAGE_MODEL_VALUE) {
            ts.value = prevValue;
            openCookbookForImg2img();
            return;
          }
          prevValue = ts.value;
          try { localStorage.setItem(key, ts.value); } catch {}
        });
      }
      inpaintPicker?.sync();
    } catch (e) {
      // Fetch failed — still give the user the affordance to set up
      // a model. Otherwise the dropdown shows only "Auto" with no
      // hint about what to do next.
      const fallback = '<option value="">Auto</option><option value="" disabled>──────────</option><option value="__serve_cookbook__">+ Serve a model in Cookbook…</option>';
      if (aiGenSelect) aiGenSelect.innerHTML = fallback;
      if (aiInpaintSelect) aiInpaintSelect.innerHTML = fallback;
      document.querySelectorAll('select.ge-tool-model').forEach(ts => { ts.innerHTML = fallback; });
      const wireServe = (sel) => {
        if (!sel) return;
        let prev = sel.value;
        sel.addEventListener('change', () => {
          if (sel.value === SERVE_IMAGE_MODEL_VALUE) {
            sel.value = prev;
            openCookbookForImg2img();
            return;
          }
          prev = sel.value;
        });
      };
      wireServe(aiGenSelect);
      wireServe(aiInpaintSelect);
      document.querySelectorAll('select.ge-tool-model').forEach(wireServe);
      inpaintPicker?.sync();
    }
  }
  loadAIModels();
  const onModelEndpointsUpdated = (e) => {
    if (!container.isConnected) {
      window.removeEventListener('ge:model-endpoints-updated', onModelEndpointsUpdated);
      return;
    }
    loadAIModels({ selectBaseUrl: e.detail?.baseUrl || '' });
  };
  window.addEventListener('ge:model-endpoints-updated', onModelEndpointsUpdated);
  // Re-fetch the model list when the user opens the inpaint dropdown,
  // so a model served via Cookbook mid-edit shows up without having to
  // close and reopen the editor. Debounced to one refresh per 3s so
  // rapid open/close doesn't hammer the endpoint. Preserves the
  // current selection across the reload.
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
  container.addEventListener('mousedown', refreshOnOpen, true);
}
