// Canonical metadata for the existing Settings information architecture.
//
// This module describes Settings; it does not render the sidebar, load panel
// data, or own panel behavior. Keeping those concerns separate lets the
// current markup remain stable while navigation/search code shares one source
// of truth for panel identity and ownership.

function defineGroup(definition) {
  return Object.freeze({ ...definition });
}

// Section scopes describe who a card's values apply to:
// - 'model-default': per account for signed-in users, server-wide for admins
//   (see settings/modelDefaults.js);
// - 'server': one value for everyone; only an administrator may change it.
// Sections without a scope manage their own storage.
export const SETTINGS_SECTION_SCOPES = Object.freeze(['model-default', 'server']);

function defineSection(definition) {
  return Object.freeze({
    description: '',
    scope: '',
    ...definition,
    keywords: Object.freeze([...(definition.keywords || [])]),
  });
}

// Sections are the searchable, deep-linkable cards inside a panel. Each one is
// anchored in the DOM by a `data-settings-section` attribute on its card, and
// getSettingsRegistryIssues() verifies the two stay in sync.
function definePanel(definition) {
  return Object.freeze({
    controller: 'settings',
    adminOnly: false,
    requiresAccounts: false,
    ...definition,
    keywords: Object.freeze([...(definition.keywords || [])]),
    sections: Object.freeze((definition.sections || []).map(defineSection)),
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
    // Creating and listing endpoints is admin-only on the server.
    adminOnly: true,
    keywords: ['models', 'provider', 'endpoint'],
    sections: [
      {
        id: 'local-models',
        label: 'Add local models',
        description: 'Connect a local model server such as Ollama, llama.cpp or vLLM.',
        keywords: ['local', 'ollama', 'llama.cpp', 'vllm', 'lm studio', 'endpoint', 'url', 'scan network'],
      },
      {
        id: 'api-models',
        label: 'Add API models',
        description: 'Connect a cloud provider with an API key.',
        keywords: ['api', 'api key', 'cloud', 'openai', 'anthropic', 'deepseek', 'openrouter', 'gemini', 'provider', 'proxy'],
      },
    ],
  }),
  definePanel({
    id: 'added-models',
    label: 'Added Models',
    group: 'models',
    controller: 'admin',
    adminOnly: true,
    keywords: ['models', 'configured', 'provider', 'endpoint'],
    sections: [
      {
        id: 'endpoints',
        label: 'Added models',
        description: 'Review, probe, enable or remove configured endpoints.',
        keywords: ['endpoints', 'probe', 'offline', 'enable', 'disable', 'remove', 'tools', 'native tool calling'],
      },
      {
        id: 'per-model-settings',
        label: 'Per-model settings',
        description: 'Generation parameters for one endpoint and model.',
        keywords: ['temperature', 'top p', 'top k', 'min p', 'presence penalty', 'repeat penalty', 'context window', 'thinking', 'sampling'],
      },
    ],
  }),
  definePanel({
    id: 'ai',
    label: 'AI Defaults',
    group: 'models',
    keywords: ['ai', 'defaults', 'model', 'vision', 'image', 'tts', 'stt'],
    sections: [
      {
        id: 'default-chat',
        scope: 'model-default',
        label: 'Default chat model',
        description: 'Model used for new chats, with optional fallback when it is unavailable.',
        keywords: ['default model', 'default', 'chat', 'model', 'endpoint', 'fallback', 'fallback order', 'unavailable', 'strict'],
      },
      {
        id: 'utility-model',
        scope: 'model-default',
        label: 'Utility model',
        description: 'Model for titles, summaries and background work.',
        keywords: ['utility', 'background', 'titles', 'summaries', 'effort', 'reasoning', 'fallback'],
      },
      {
        id: 'vision',
        scope: 'server',
        label: 'Vision',
        description: 'Model that describes images and attachments.',
        keywords: ['vision', 'image understanding', 'attachments', 'fallback'],
      },
      {
        id: 'research-model',
        scope: 'model-default',
        label: 'Research model',
        description: 'Model used by Deep Research.',
        keywords: ['research', 'deep research', 'model', 'endpoint'],
      },
      {
        id: 'image-generation',
        label: 'Image generation',
        description: 'Model and quality for generated and edited images.',
        keywords: ['image', 'generation', 'edit', 'quality', 'flux', 'dall-e'],
      },
      {
        id: 'text-to-speech',
        scope: 'server',
        label: 'Text to speech',
        description: 'Provider, model, voice and speed for read-aloud.',
        keywords: ['tts', 'speech', 'voice', 'read aloud', 'kokoro', 'speed'],
      },
      {
        id: 'writing-style',
        scope: 'server',
        label: 'Writing style',
        description: 'General voice for documents.',
        keywords: ['writing', 'style', 'tone', 'voice', 'documents'],
      },
    ],
  }),
  definePanel({
    id: 'search',
    label: 'Search',
    group: 'models',
    keywords: ['search', 'research', 'provider'],
    sections: [
      {
        id: 'web-search',
        scope: 'server',
        label: 'Web search',
        description: 'Search provider, API keys and fallback providers.',
        keywords: ['web', 'provider', 'searxng', 'duckduckgo', 'brave', 'google', 'tavily', 'serper', 'you.com', 'api key', 'results', 'fallback'],
      },
      {
        id: 'deep-research',
        scope: 'server',
        label: 'Deep research',
        description: 'Deep Research search provider, token and timeout limits.',
        keywords: ['research', 'deep research', 'max tokens', 'timeout', 'extract', 'parallel'],
      },
    ],
  }),

  definePanel({
    id: 'integrations',
    label: 'Integrations',
    group: 'communications',
    controller: 'admin',
    keywords: ['integrations', 'connections', 'services'],
    sections: [
      {
        id: 'integrations',
        label: 'Integrations',
        description: 'All external service connections in one place.',
        keywords: ['integrations', 'connections', 'mcp', 'ntfy', 'webhook', 'calendar', 'caldav', 'carddav', 'contacts', 'vault', 'oauth'],
      },
    ],
  }),
  definePanel({
    id: 'email',
    label: 'Email',
    group: 'communications',
    keywords: ['email', 'imap', 'smtp', 'oauth'],
    sections: [
      {
        id: 'email-default',
        label: 'Default email account',
        description: 'Set the default email and which settings apply here.',
        keywords: ['email', 'default', 'account'],
      },
      {
        id: 'email-accounts',
        label: 'Email accounts',
        description: 'Add, edit, delete and test accounts in Integrations.',
        keywords: ['email', 'accounts', 'imap', 'smtp', 'oauth', 'gmail', 'outlook'],
      },
      {
        id: 'email-tasks',
        label: 'Email tasks',
        description: 'Manage email background tasks in Tasks.',
        keywords: ['email', 'tasks', 'background', 'triage'],
      },
      {
        id: 'email-preferences',
        label: 'Email preferences',
        description: 'Signature, greetings and email writing style.',
        keywords: ['email', 'preferences', 'signature', 'writing style', 'greeting'],
      },
    ],
  }),
  definePanel({
    id: 'reminders',
    label: 'Reminders',
    group: 'communications',
    keywords: ['reminders', 'notifications', 'alerts'],
    sections: [
      {
        id: 'delivery',
        scope: 'server',
        label: 'Reminder delivery',
        description: 'How fired note reminders are delivered.',
        keywords: ['reminders', 'channel', 'browser notification', 'email', 'ntfy', 'webhook', 'test'],
      },
      {
        id: 'ai-synthesis',
        scope: 'server',
        label: 'AI reminder synthesis',
        description: 'Let the utility model write a short reminder line.',
        keywords: ['ai', 'synthesis', 'persona', 'utility'],
      },
      {
        id: 'public-url',
        scope: 'server',
        label: 'Public app URL',
        description: 'Base URL for links inside outgoing emails.',
        keywords: ['public', 'url', 'links', 'domain', 'base url'],
      },
    ],
  }),

  definePanel({
    id: 'appearance',
    label: 'Appearance',
    group: 'experience',
    keywords: ['appearance', 'theme', 'font', 'density', 'peek'],
    sections: [
      {
        id: 'sidebar',
        label: 'Sidebar items',
        description: 'Show or hide items in the main sidebar.',
        keywords: ['sidebar', 'hide', 'show', 'visibility', 'navigation', 'menu'],
      },
      {
        id: 'chat-area',
        label: 'Chat area',
        description: 'Session header, full-width chat, incognito, thinking and sensitive blur.',
        keywords: ['chat', 'full width', 'header', 'welcome', 'incognito', 'emojis', 'thinking', 'blur', 'sensitive', 'privacy'],
      },
      {
        id: 'chat-bar',
        label: 'Chat bar',
        description: 'Choose which buttons appear in the message composer.',
        keywords: ['chat bar', 'composer', 'buttons', 'web search', 'shell', 'attach', 'personas', 'agent mode'],
      },
    ],
  }),
  definePanel({
    id: 'shortcuts',
    label: 'Shortcuts',
    group: 'experience',
    keywords: ['shortcuts', 'keyboard', 'hotkeys'],
    sections: [
      {
        id: 'keyboard-shortcuts',
        label: 'Keyboard shortcuts',
        description: 'Click a shortcut to rebind it.',
        keywords: ['keyboard', 'shortcuts', 'hotkeys', 'keybindings', 'rebind'],
      },
    ],
  }),

  definePanel({
    id: 'account',
    label: 'Account',
    group: 'account',
    keywords: ['account', 'password', 'logout'],
    sections: [
      {
        id: 'account',
        label: 'Account',
        description: 'Signed-in user and logout.',
        keywords: ['account', 'user', 'logout', 'sign out'],
      },
      {
        id: 'password',
        label: 'Change password',
        keywords: ['password', 'change password', 'security'],
      },
      {
        id: 'two-factor',
        label: 'Two-factor authentication',
        keywords: ['2fa', 'two-factor', 'totp', 'authenticator', 'security'],
      },
      {
        id: 'api-tokens',
        label: 'Personal API tokens',
        description: 'Create and revoke scoped API tokens.',
        keywords: ['api', 'tokens', 'personal access token', 'scopes', 'revoke'],
      },
    ],
  }),

  definePanel({
    id: 'tools',
    label: 'Agent Tools',
    group: 'administration',
    controller: 'admin',
    adminOnly: true,
    keywords: ['agent', 'tools'],
    sections: [
      {
        id: 'agent-limits',
        label: 'Agent limits and approval',
        description: 'Tool call limit, steps per message and tool approval.',
        keywords: ['agent', 'tool call limit', 'max steps', 'rounds', 'approval', 'tool approval', 'permissions', 'yolo'],
      },
      {
        id: 'builtin-tools',
        label: 'Built-in tools',
        description: 'Enable or disable tools available to the agent.',
        keywords: ['tools', 'built-in', 'enable', 'disable'],
      },
    ],
  }),
  definePanel({
    id: 'users',
    label: 'Users',
    group: 'administration',
    controller: 'admin',
    adminOnly: true,
    // User management needs real accounts; with authentication disabled the
    // server refuses it, so the panel is not offered.
    requiresAccounts: true,
    keywords: ['users', 'accounts', 'admin'],
    sections: [
      {
        id: 'registration',
        label: 'Registration',
        description: 'Allow anyone to create an account from the login page.',
        keywords: ['signup', 'registration', 'open signup'],
      },
      {
        id: 'model-defaults',
        label: 'Shared model defaults',
        description: 'Let users without a personal default inherit the global default model.',
        keywords: ['share defaults', 'default model', 'users'],
      },
      {
        id: 'user-list',
        label: 'Users',
        description: 'Manage accounts, admin roles and privileges.',
        keywords: ['users', 'accounts', 'admin', 'privileges', 'reset password'],
      },
      {
        id: 'add-user',
        label: 'Add user',
        keywords: ['add user', 'create account'],
      },
    ],
  }),
  definePanel({
    id: 'system',
    label: 'System',
    group: 'administration',
    controller: 'admin',
    adminOnly: true,
    keywords: ['system', 'admin', 'server'],
    sections: [
      {
        id: 'logs',
        label: 'Logs',
        description: 'Terminal output and task notification logs.',
        keywords: ['logs', 'terminal', 'notifications', 'debug', 'errors', 'diagnostics'],
      },
      {
        id: 'backup',
        label: 'Data backup',
        description: 'Export or import memories, presets, settings and skills.',
        keywords: ['backup', 'export', 'import', 'restore'],
      },
      {
        id: 'danger-zone',
        label: 'Danger zone',
        description: 'Irreversibly delete one category of data.',
        keywords: ['delete', 'wipe', 'reset', 'danger'],
      },
    ],
  }),
]);

export const DEFAULT_SETTINGS_PANEL_ID = 'services';

/** The first panel, in sidebar order, that this caller is offered. */
export function getFirstOfferedSettingsPanelId(options = {}) {
  const panel = SETTINGS_PANELS.find(candidate => isSettingsPanelOffered(candidate, options));
  return panel ? panel.id : DEFAULT_SETTINGS_PANEL_ID;
}

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

/**
 * Whether a panel is offered to this caller. `isAdmin` means "may change
 * server-wide settings"; `accountsEnabled` is false when authentication is
 * disabled. Both default to the most restrictive reading.
 */
export function isSettingsPanelOffered(panelOrId, options = {}) {
  const panel = typeof panelOrId === 'string' ? getSettingsPanel(panelOrId) : panelOrId;
  if (!panel) return false;
  if (panel.adminOnly && options.isAdmin !== true) return false;
  if (panel.requiresAccounts && options.accountsEnabled === false) return false;
  return true;
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

export function getSettingsSection(panelId, sectionId) {
  const panel = getSettingsPanel(panelId);
  if (!panel) return null;
  return panel.sections.find(section => section.id === sectionId) || null;
}

// Rank one field against the query. Higher is better; 0 means the field does
// not contribute. The weights only order results; every term must still match
// somewhere in the entry for it to be returned at all.
function scoreField(text, normalized, terms, weight) {
  const value = String(text || '').toLowerCase();
  if (!value) return 0;
  if (value === normalized) return weight * 4;
  if (value.startsWith(normalized)) return weight * 3;
  if (value.includes(normalized)) return weight * 2;
  const words = value.split(/[^a-z0-9.]+/).filter(Boolean);
  const wordHits = terms.filter(term => words.some(word => word.startsWith(term))).length;
  return wordHits ? weight * (wordHits / terms.length) : 0;
}

function scoreEntry(fields, normalized, terms) {
  const haystack = fields.map(field => String(field.text || '').toLowerCase()).join(' ');
  if (!terms.every(term => haystack.includes(term))) return 0;
  return fields.reduce(
    (best, field) => Math.max(best, scoreField(field.text, normalized, terms, field.weight)),
    0,
  ) || 1;
}

/**
 * Search panels and the sections inside them.
 *
 * Returns entries shaped as `{ kind: 'panel'|'section', panel, section,
 * score }`, best match first and registry order as the tie-breaker. Admin-only
 * panels (and their sections) are omitted for non-admin users. Whether a
 * section is currently visible in the DOM is the caller's concern.
 */
export function searchSettings(query, options = {}) {
  const normalized = normalizeSettingsSearch(query);
  if (!normalized) return [];

  const terms = normalized.split(' ');
  const results = [];
  let order = 0;

  for (const panel of SETTINGS_PANELS) {
    if (!isSettingsPanelOffered(panel, options)) continue;

    const panelScore = scoreEntry([
      { text: panel.label, weight: 10 },
      ...panel.keywords.map(text => ({ text, weight: 5 })),
    ], normalized, terms);
    if (panelScore) {
      results.push({ kind: 'panel', panel, section: null, score: panelScore, order: order });
    }
    order += 1;

    for (const section of panel.sections) {
      const sectionScore = scoreEntry([
        { text: section.label, weight: 9 },
        ...section.keywords.map(text => ({ text, weight: 6 })),
        { text: section.description, weight: 3 },
        { text: panel.label, weight: 2 },
      ], normalized, terms);
      if (sectionScore) {
        results.push({ kind: 'section', panel, section, score: sectionScore, order: order });
      }
      order += 1;
    }
  }

  results.sort((a, b) => (b.score - a.score) || (a.order - b.order));
  return results.map(({ order: _order, ...entry }) => entry);
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

  // Section anchors must live inside their own panel, once each.
  const panelElements = Array.from(modalEl.querySelectorAll('[data-settings-panel]'));
  for (const panel of SETTINGS_PANELS) {
    const panelEl = panelElements.find(element => element.dataset.settingsPanel === panel.id);
    if (!panelEl) continue;

    const sectionIds = Array.from(
      panelEl.querySelectorAll('[data-settings-section]'),
      element => element.dataset.settingsSection,
    ).filter(Boolean);
    const declared = panel.sections.map(section => section.id);

    for (const id of new Set(duplicates(sectionIds))) {
      issues.push(`Duplicate Settings section: ${panel.id}/${id}`);
    }
    for (const id of declared) {
      if (!sectionIds.includes(id)) issues.push(`Registry section missing from DOM: ${panel.id}/${id}`);
    }
    for (const id of sectionIds) {
      if (!declared.includes(id)) issues.push(`DOM section missing from registry: ${panel.id}/${id}`);
    }
  }

  return issues;
}
