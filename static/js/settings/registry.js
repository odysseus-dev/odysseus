// Canonical metadata for the existing Settings information architecture.
//
// This module describes Settings; it does not render the sidebar, load panel
// data, or own panel behavior. Keeping those concerns separate lets the
// current markup remain stable while navigation/search code shares one source
// of truth for panel identity and ownership.

function defineGroup(definition) {
  return Object.freeze({ ...definition });
}

function definePanel(definition) {
  return Object.freeze({
    controller: 'settings',
    adminOnly: false,
    ...definition,
    keywords: Object.freeze([...(definition.keywords || [])]),
  });
}

export const SETTINGS_GROUPS = Object.freeze([
  defineGroup({
    id: 'models',
    label: 'Models & AI',
  }),
  defineGroup({
    id: 'communications',
    label: 'Communications',
  }),
  defineGroup({
    id: 'experience',
    label: 'Experience',
  }),
  defineGroup({
    id: 'account',
    label: 'Account',
  }),
  defineGroup({
    id: 'administration',
    label: 'Administration',
    adminOnly: true,
  }),
]);

// Order intentionally mirrors the existing Settings sidebar.
export const SETTINGS_PANELS = Object.freeze([
  definePanel({
    id: 'services',
    label: 'Add Models',
    group: 'models',
    controller: 'admin',
    keywords: ['models', 'provider', 'endpoint'],
  }),
  definePanel({
    id: 'added-models',
    label: 'Added Models',
    group: 'models',
    controller: 'admin',
    keywords: ['models', 'configured', 'provider', 'endpoint'],
  }),
  definePanel({
    id: 'ai',
    label: 'AI Defaults',
    group: 'models',
    keywords: ['ai', 'defaults', 'model', 'vision', 'image', 'tts', 'stt'],
  }),
  definePanel({
    id: 'search',
    label: 'Search',
    group: 'models',
    keywords: ['search', 'research', 'provider'],
  }),

  definePanel({
    id: 'integrations',
    label: 'Integrations',
    group: 'communications',
    controller: 'admin',
    keywords: ['integrations', 'connections', 'services'],
  }),
  definePanel({
    id: 'email',
    label: 'Email',
    group: 'communications',
    keywords: ['email', 'imap', 'smtp', 'oauth'],
  }),
  definePanel({
    id: 'reminders',
    label: 'Reminders',
    group: 'communications',
    keywords: ['reminders', 'notifications', 'alerts'],
  }),

  definePanel({
    id: 'appearance',
    label: 'Appearance',
    group: 'experience',
    keywords: ['appearance', 'theme', 'font', 'density', 'peek'],
  }),
  definePanel({
    id: 'shortcuts',
    label: 'Shortcuts',
    group: 'experience',
    keywords: ['shortcuts', 'keyboard', 'hotkeys'],
  }),

  definePanel({
    id: 'account',
    label: 'Account',
    group: 'account',
    keywords: ['account', 'password', 'logout'],
  }),

  definePanel({
    id: 'tools',
    label: 'Agent Tools',
    group: 'administration',
    controller: 'admin',
    adminOnly: true,
    keywords: ['agent', 'tools'],
  }),
  definePanel({
    id: 'users',
    label: 'Users',
    group: 'administration',
    controller: 'admin',
    adminOnly: true,
    keywords: ['users', 'accounts', 'admin'],
  }),
  definePanel({
    id: 'system',
    label: 'System',
    group: 'administration',
    controller: 'admin',
    adminOnly: true,
    keywords: ['system', 'admin', 'server'],
  }),
]);

export const DEFAULT_SETTINGS_PANEL_ID = 'services';

const _panelsById = new Map(
  SETTINGS_PANELS.map(panel => [panel.id, panel]),
);

export function getSettingsPanel(id) {
  return _panelsById.get(String(id || '')) || null;
}

export function getSettingsPanelsForGroup(groupId) {
  return SETTINGS_PANELS.filter(panel => panel.group === groupId);
}

export function isAdminManagedSettingsTab(id) {
  return getSettingsPanel(id)?.controller === 'admin';
}

export function isAdminOnlySettingsTab(id) {
  return getSettingsPanel(id)?.adminOnly === true;
}

export function getSettingsPanelSearchText(panelOrId) {
  const panel = typeof panelOrId === 'string'
    ? getSettingsPanel(panelOrId)
    : panelOrId;

  if (!panel) return '';

  return [
    panel.label,
    ...(panel.keywords || []),
  ].join(' ').toLowerCase();
}

function normalizeSettingsSearch(value) {
  return String(value || '')
    .trim()
    .toLowerCase()
    .replace(/\s+/g, ' ');
}

export function searchSettingsPanels(query, options = {}) {
  const normalized = normalizeSettingsSearch(query);
  if (!normalized) return [];

  const terms = normalized.split(' ');
  const isAdmin = options.isAdmin === true;

  return SETTINGS_PANELS.filter(panel => {
    if (panel.adminOnly && !isAdmin) return false;

    const haystack = getSettingsPanelSearchText(panel);
    return terms.every(term => haystack.includes(term));
  });
}

export function getSettingsRegistryIssues(modalEl) {
  if (!modalEl) return ['Settings modal is unavailable'];

  const tabIds = Array.from(
    modalEl.querySelectorAll('[data-settings-tab]'),
    element => element.dataset.settingsTab,
  ).filter(Boolean);

  const panelIds = Array.from(
    modalEl.querySelectorAll('[data-settings-panel]'),
    element => element.dataset.settingsPanel,
  ).filter(Boolean);

  const registryIds = SETTINGS_PANELS.map(panel => panel.id);
  const issues = [];

  const duplicates = ids => ids.filter(
    (id, index) => ids.indexOf(id) !== index,
  );

  for (const id of new Set(duplicates(tabIds))) {
    issues.push(`Duplicate Settings tab: ${id}`);
  }
  for (const id of new Set(duplicates(panelIds))) {
    issues.push(`Duplicate Settings panel: ${id}`);
  }

  for (const id of registryIds) {
    if (!tabIds.includes(id)) issues.push(`Registry tab missing from DOM: ${id}`);
    if (!panelIds.includes(id)) issues.push(`Registry panel missing from DOM: ${id}`);
  }

  for (const id of tabIds) {
    if (!registryIds.includes(id)) issues.push(`DOM tab missing from registry: ${id}`);
  }
  for (const id of panelIds) {
    if (!registryIds.includes(id)) issues.push(`DOM panel missing from registry: ${id}`);
  }

  return issues;
}
