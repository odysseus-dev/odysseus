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
    i18nKey: 'ui.models.ai',
  }),
  defineGroup({
    id: 'communications',
    label: 'Communications',
    i18nKey: 'ui.communications',
  }),
  defineGroup({
    id: 'experience',
    label: 'Experience',
    i18nKey: 'ui.experience',
  }),
  defineGroup({
    id: 'account',
    label: 'Account',
    i18nKey: 'ui.account',
  }),
  defineGroup({
    id: 'administration',
    label: 'Administration',
    i18nKey: 'ui.administration',
    adminOnly: true,
  }),
]);

// Order intentionally mirrors the existing Settings sidebar.
export const SETTINGS_PANELS = Object.freeze([
  definePanel({
    id: 'services',
    label: 'Add Models',
    i18nKey: 'ui.add.models',
    group: 'models',
    controller: 'admin',
    keywords: ['models', 'provider', 'endpoint'],
  }),
  definePanel({
    id: 'added-models',
    label: 'Added Models',
    i18nKey: 'ui.added.models',
    group: 'models',
    controller: 'admin',
    keywords: ['models', 'configured', 'provider', 'endpoint'],
  }),
  definePanel({
    id: 'ai',
    label: 'AI Defaults',
    i18nKey: 'ui.ai.defaults',
    group: 'models',
    keywords: ['ai', 'defaults', 'model', 'vision', 'image', 'tts', 'stt'],
  }),
  definePanel({
    id: 'search',
    label: 'Search',
    i18nKey: 'ui.search',
    group: 'models',
    keywords: ['search', 'research', 'provider'],
  }),

  definePanel({
    id: 'integrations',
    label: 'Integrations',
    i18nKey: 'ui.integrations',
    group: 'communications',
    controller: 'admin',
    keywords: ['integrations', 'connections', 'services'],
  }),
  definePanel({
    id: 'email',
    label: 'Email',
    i18nKey: 'ui.email',
    group: 'communications',
    keywords: ['email', 'imap', 'smtp', 'oauth'],
  }),
  definePanel({
    id: 'reminders',
    label: 'Reminders',
    i18nKey: 'ui.reminders.ae8c3939',
    group: 'communications',
    keywords: ['reminders', 'notifications', 'alerts'],
  }),

  definePanel({
    id: 'appearance',
    label: 'Appearance',
    i18nKey: 'ui.appearance',
    group: 'experience',
    keywords: ['appearance', 'theme', 'font', 'density', 'peek'],
  }),
  definePanel({
    id: 'shortcuts',
    label: 'Shortcuts',
    i18nKey: 'ui.shortcuts',
    group: 'experience',
    keywords: ['shortcuts', 'keyboard', 'hotkeys'],
  }),

  definePanel({
    id: 'account',
    label: 'Account',
    i18nKey: 'ui.account',
    group: 'account',
    keywords: ['account', 'password', 'logout'],
  }),

  definePanel({
    id: 'tools',
    label: 'Agent Tools',
    i18nKey: 'ui.agent.tools',
    group: 'administration',
    controller: 'admin',
    adminOnly: true,
    keywords: ['agent', 'tools'],
  }),
  definePanel({
    id: 'users',
    label: 'Users',
    i18nKey: 'ui.users',
    group: 'administration',
    controller: 'admin',
    adminOnly: true,
    keywords: ['users', 'accounts', 'admin'],
  }),
  definePanel({
    id: 'system',
    label: 'System',
    i18nKey: 'ui.system',
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

export function getSettingsPanelSearchText(panelOrId, options = {}) {
  const panel = typeof panelOrId === 'string'
    ? getSettingsPanel(panelOrId)
    : panelOrId;

  if (!panel) return '';

  const group = SETTINGS_GROUPS.find(candidate => candidate.id === panel.group);
  const translate = typeof options.translate === 'function' ? options.translate : null;
  const translated = translate
    ? [
        translate(panel.i18nKey, panel.label),
        translate(group?.i18nKey, group?.label || ''),
      ]
    : [];
  return [
    panel.label,
    ...(panel.keywords || []),
    group?.label || '',
    ...translated,
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

    const haystack = getSettingsPanelSearchText(panel, options);
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
