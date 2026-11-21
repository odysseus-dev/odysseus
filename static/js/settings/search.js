import {
  SETTINGS_GROUPS,
  getSettingsPanel,
  getSettingsSection,
  searchSettings,
} from './registry.js';
import {
  findSettingsSectionElement,
  isSettingsSectionAvailable,
  revealSettingsSection,
} from './navigation.js';

const _boundModals = new WeakSet();
const MAX_RESULTS = 12;

function groupLabelFor(panel) {
  const group = SETTINGS_GROUPS.find(candidate => candidate.id === panel.group);
  return group?.label || '';
}

function getResultButtons(resultsEl) {
  if (!resultsEl) return [];
  return Array.from(resultsEl.querySelectorAll('[data-settings-search-result]'));
}

export function bindSettingsSearch(modalEl, options = {}) {
  if (!modalEl || _boundModals.has(modalEl)) return;

  const input = modalEl.querySelector('#settings-nav-search');
  const resultsEl = modalEl.querySelector('#settings-nav-search-results');

  if (!input || !resultsEl) return;
  _boundModals.add(modalEl);

  const isAdmin = typeof options.isAdmin === 'function'
    ? options.isAdmin
    : () => false;

  const accountsEnabled = typeof options.accountsEnabled === 'function'
    ? options.accountsEnabled
    : () => true;

  const openPanel = typeof options.openPanel === 'function'
    ? options.openPanel
    : () => {};

  const revealSection = typeof options.revealSection === 'function'
    ? options.revealSection
    : (panelId, sectionId) => revealSettingsSection(modalEl, panelId, sectionId);

  let activeIndex = -1;

  input.setAttribute('role', 'combobox');
  input.setAttribute('aria-autocomplete', 'list');
  input.setAttribute('aria-expanded', 'false');

  function setExpanded(expanded) {
    resultsEl.classList.toggle('hidden', !expanded);
    input.setAttribute('aria-expanded', expanded ? 'true' : 'false');
    if (!expanded) input.removeAttribute?.('aria-activedescendant');
  }

  function clearResults() {
    resultsEl.replaceChildren();
    activeIndex = -1;
    setExpanded(false);
  }

  function setActiveResult(index) {
    const buttons = getResultButtons(resultsEl);
    if (!buttons.length) return -1;

    let next = index;
    if (next < 0) next = buttons.length - 1;
    if (next >= buttons.length) next = 0;

    buttons.forEach((button, buttonIndex) => {
      const active = buttonIndex === next;
      button.classList.toggle('active', active);
      button.setAttribute('aria-selected', active ? 'true' : 'false');
    });

    if (buttons[next].id) input.setAttribute('aria-activedescendant', buttons[next].id);
    if (typeof buttons[next].scrollIntoView === 'function') {
      buttons[next].scrollIntoView({ block: 'nearest' });
    }

    return next;
  }

  function reset() {
    input.value = '';
    clearResults();
  }

  function activateResult(button) {
    const panelId = button?.dataset?.settingsSearchResult;
    if (!panelId || !getSettingsPanel(panelId)) return;
    const sectionId = button.dataset.settingsSearchSection || '';

    openPanel(panelId);
    reset();
    if (sectionId && getSettingsSection(panelId, sectionId)) {
      revealSection(panelId, sectionId);
    }
  }

  // A section is offered only while its card is actually shown: feature-gated
  // or not-yet-loaded cards are hidden in the DOM and must not become dead
  // results.
  function sectionIsAvailable(panelId, sectionId, admin) {
    const element = findSettingsSectionElement(modalEl, panelId, sectionId);
    return Boolean(element) && isSettingsSectionAvailable(element, { isAdmin: admin });
  }

  function buildResult(entry, index) {
    const button = document.createElement('button');
    button.type = 'button';
    button.className = 'settings-search-result';
    button.id = `settings-search-option-${index}`;
    button.setAttribute('data-settings-search-result', entry.panel.id);
    if (entry.section) {
      button.setAttribute('data-settings-search-section', entry.section.id);
    }
    button.setAttribute('role', 'option');
    button.setAttribute('aria-selected', 'false');

    const label = document.createElement('span');
    label.className = 'settings-search-result-label';
    label.textContent = entry.section ? entry.section.label : entry.panel.label;

    const group = document.createElement('span');
    group.className = 'settings-search-result-group';
    group.textContent = entry.section ? entry.panel.label : groupLabelFor(entry.panel);

    button.append(label, group);

    if (entry.section && entry.section.description) {
      const description = document.createElement('span');
      description.className = 'settings-search-result-description';
      description.textContent = entry.section.description;
      button.appendChild(description);
    }

    button.addEventListener('click', () => activateResult(button));
    return button;
  }

  function render() {
    const query = input.value.trim();
    resultsEl.replaceChildren();
    activeIndex = -1;
    input.removeAttribute?.('aria-activedescendant');

    if (!query) {
      setExpanded(false);
      return;
    }

    const admin = isAdmin() === true;
    const matches = searchSettings(query, { isAdmin: admin, accountsEnabled: accountsEnabled() !== false })
      .filter(entry => !entry.section
        || sectionIsAvailable(entry.panel.id, entry.section.id, admin))
      .slice(0, MAX_RESULTS);

    if (!matches.length) {
      const empty = document.createElement('div');
      empty.className = 'settings-search-empty';
      empty.setAttribute('role', 'status');
      empty.textContent = 'No settings found';
      resultsEl.appendChild(empty);
      setExpanded(true);
      return;
    }

    const countStatus = document.createElement('div');
    countStatus.className = 'a11y-visually-hidden';
    countStatus.setAttribute('role', 'status');
    countStatus.setAttribute('aria-live', 'polite');
    countStatus.textContent = `${matches.length} result${matches.length === 1 ? '' : 's'} available`;
    resultsEl.appendChild(countStatus);

    matches.forEach((entry, index) => {
      resultsEl.appendChild(buildResult(entry, index));
    });

    setExpanded(true);
  }

  input.addEventListener('input', render);

  input.addEventListener('focus', () => {
    if (input.value.trim()) render();
  });

  input.addEventListener('keydown', event => {
    const buttons = getResultButtons(resultsEl);

    if (event.key === 'Escape') {
      if (!input.value && resultsEl.classList.contains('hidden')) return;
      event.preventDefault();
      event.stopPropagation();
      reset();
      return;
    }

    if (!buttons.length) return;

    if (event.key === 'ArrowDown') {
      event.preventDefault();
      activeIndex = setActiveResult(activeIndex + 1);
      return;
    }

    if (event.key === 'ArrowUp') {
      event.preventDefault();
      activeIndex = setActiveResult(activeIndex - 1);
      return;
    }

    if (event.key === 'Enter') {
      const target = buttons[activeIndex >= 0 ? activeIndex : 0];
      if (!target) return;
      event.preventDefault();
      activateResult(target);
    }
  });

  modalEl.addEventListener('mousedown', event => {
    if (event.target === input || resultsEl.contains(event.target)) return;
    resultsEl.replaceChildren();
    activeIndex = -1;
    setExpanded(false);
  });

  return { reset, render };
}
