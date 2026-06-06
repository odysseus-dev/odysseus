/**
 * Shared command palette shell — same markup/classes as search-chat (Ctrl+K).
 */

/**
 * @param {string} id - Overlay element id (e.g. email-cmd-palette)
 * @param {{ headerText?: string, inputId?: string, resultsId?: string, placeholder?: string }} [opts]
 */
export function createCommandPaletteOverlay(id, {
  headerText = '',
  inputId = '',
  resultsId = '',
  placeholder = '',
} = {}) {
  const overlay = document.createElement('div');
  overlay.className = 'search-overlay';
  overlay.id = id;

  const popup = document.createElement('div');
  popup.className = 'search-popup';

  if (headerText) {
    const header = document.createElement('div');
    header.className = 'search-group-header';
    header.textContent = headerText;
    popup.appendChild(header);
  }

  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'search-palette-input';
  input.autocomplete = 'off';
  if (inputId) input.id = inputId;
  if (placeholder) input.placeholder = placeholder;

  const results = document.createElement('div');
  results.className = 'search-results';
  if (resultsId) results.id = resultsId;

  popup.appendChild(input);
  popup.appendChild(results);
  overlay.appendChild(popup);

  return { overlay, popup, input, results };
}

export function wireCommandPaletteDismiss(overlay, close) {
  overlay.addEventListener('click', (ev) => {
    if (ev.target === overlay) close();
  });
}

/** @param {HTMLElement} resultsEl */
export function setCommandPaletteEmpty(resultsEl, message) {
  if (!resultsEl) return;
  resultsEl.innerHTML = `<div class="search-empty">${message}</div>`;
}

/**
 * @param {HTMLElement} resultsEl
 * @param {Array<{ label: string, meta?: string, onSelect: () => void }>} items
 */
export function renderCommandPaletteItems(resultsEl, items) {
  if (!resultsEl) return;
  resultsEl.innerHTML = '';
  if (!items.length) {
    setCommandPaletteEmpty(resultsEl, 'No results found');
    return;
  }
  items.forEach((item) => {
    const row = document.createElement('div');
    row.className = 'search-result-item';
    row.setAttribute('role', 'button');
    row.tabIndex = -1;
    if (item.meta) {
      row.innerHTML = `
        <div class="search-result-snippet">${item.label}</div>
        <div class="search-result-time">${item.meta}</div>`;
    } else {
      row.innerHTML = `<div class="search-result-snippet">${item.label}</div>`;
    }
    row.addEventListener('click', item.onSelect);
    resultsEl.appendChild(row);
  });
}
