// Stateless helpers that fill the endpoint and model <select>s of the model
// cards and keep their provider logos in step. Extracted verbatim from
// settings.js.

import { providerLogo } from '../providers.js';
import { sortModelIds } from '../modelSort.js';

export function ensureBlankOption(selectEl, text) {
  if (!selectEl) return;
  var first = selectEl.options[0];
  if (first && first.value === '') { first.textContent = text; return; }
  var blank = document.createElement('option');
  blank.value = '';
  blank.textContent = text;
  selectEl.insertBefore(blank, first || null);
}

export function selectableEndpointModels(endpoint) {
  if (!endpoint) return [];
  return Array.from(new Set([
    ...(Array.isArray(endpoint.models) ? endpoint.models : []),
    ...(Array.isArray(endpoint.pinned_models) ? endpoint.pinned_models : []),
  ].filter(Boolean).map(String)));
}

function endpointLabel(ep) {
  return ep.name + (ep.online ? '' : ' (offline)');
}

export function fillEndpointSelect(selectEl, endpoints, selected, keepBlank) {
  if (!selectEl) return;
  const previous = selected !== undefined ? selected : selectEl.value;
  const blankText = keepBlank && selectEl.options[0] && selectEl.options[0].value === ''
    ? selectEl.options[0].textContent
    : null;
  while (selectEl.options.length) selectEl.remove(0);
  if (blankText !== null) {
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = blankText;
    selectEl.appendChild(blank);
  }
  (endpoints || []).forEach(function(ep) {
    if (!ep.is_enabled) return;
    const opt = document.createElement('option');
    opt.value = ep.id;
    opt.textContent = endpointLabel(ep);
    selectEl.appendChild(opt);
  });
  if (previous && Array.from(selectEl.options).some(function(o) { return o.value === previous; })) {
    selectEl.value = previous;
  } else if (blankText !== null) {
    selectEl.value = '';
  }
  syncEndpointLogo(selectEl);
}

// Mirror the selected model's provider logo into a sibling <span id="<selectId>-logo">.
// Wires the change listener exactly once so we can call this every time the
// select is repopulated without piling on duplicate handlers.
export function syncModelLogo(selectEl) {
  if (!selectEl) return;
  const logoEl = document.getElementById(selectEl.id + '-logo');
  if (!logoEl) return;
  const apply = () => { logoEl.innerHTML = providerLogo(selectEl.value) || ''; };
  apply();
  if (!selectEl.dataset.logoSync) {
    selectEl.dataset.logoSync = '1';
    selectEl.addEventListener('change', apply);
  }
}

// Same idea but for endpoint dropdowns where the <option value="…">
// is an opaque endpoint UUID — fall back to the option's text label
// so providerLogo() can pattern-match (Anthropic, OpenAI, Ollama, …).
export function syncEndpointLogo(selectEl) {
  if (!selectEl) return;
  const logoEl = document.getElementById(selectEl.id + '-logo');
  if (!logoEl) return;
  const apply = () => {
    const opt = selectEl.options[selectEl.selectedIndex];
    const label = (opt && opt.textContent) || selectEl.value || '';
    logoEl.innerHTML = providerLogo(label) || '';
  };
  apply();
  if (!selectEl.dataset.epLogoSync) {
    selectEl.dataset.epLogoSync = '1';
    selectEl.addEventListener('change', apply);
  }
}

export function fillModelSelect(selectEl, models, selected, keepBlank) {
  if (!selectEl) return;
  const previous = selected !== undefined ? selected : selectEl.value;
  const blankText = keepBlank && selectEl.options[0] && selectEl.options[0].value === ''
    ? selectEl.options[0].textContent
    : null;
  while (selectEl.options.length) selectEl.remove(0);
  if (blankText !== null) {
    const blank = document.createElement('option');
    blank.value = '';
    blank.textContent = blankText;
    selectEl.appendChild(blank);
  }
  sortModelIds(models).forEach(function(m) {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = String(m).split('/').pop();
    selectEl.appendChild(opt);
  });
  if (previous && Array.from(selectEl.options).some(function(o) { return o.value === previous; })) {
    selectEl.value = previous;
  } else if (blankText !== null) {
    selectEl.value = '';
  }
  syncModelLogo(selectEl);
}
