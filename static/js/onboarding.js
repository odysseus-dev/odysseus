// static/js/onboarding.js
// First-run welcome and setup flow for Odysseus.

import themeModule, { THEMES } from './theme.js';

const API_BASE = window.location.origin;
const PREF_KEY = 'onboarding_complete';
const TOTAL_STEPS = 4;

const DEFAULT_THEME = 'dark';
const DEFAULT_DENSITY = 'comfortable';
const DEFAULT_FONT = 'mono';

const THEME_DEFAULT_PATTERN = {
  dark: 'none',
  light: 'dots',
  midnight: 'rain',
  paper: 'dots',
  cyberpunk: 'synapse',
  retrowave: 'embers',
  forest: 'petals',
  ocean: 'constellations',
  gpt: 'none',
  claude: 'none',
};

const THEME_DEFAULT_EFFECT_COLOR = {
  midnight: '#ffffff',
};

const THEME_DEFAULT_INTENSITY = {
  midnight: 0.5,
};

const FEATURED_THEMES = [
  'dark',
  'midnight',
  'gpt',
  'claude',
  'light',
  'paper',
  'ocean',
  'forest',
];

const DENSITY_OPTIONS = [
  { id: 'compact', label: 'Compact' },
  { id: 'comfortable', label: 'Comfortable' },
  { id: 'spacious', label: 'Spacious' },
];

const CLOUD_PROVIDERS = [
  {
    id: 'openai',
    name: 'OpenAI',
    detail: 'GPT models',
    baseUrl: 'https://api.openai.com/v1',
    placeholder: 'sk-...',
    keyRequired: true,
    supportsTools: true,
  },
  {
    id: 'anthropic',
    name: 'Anthropic',
    detail: 'Claude models',
    baseUrl: 'https://api.anthropic.com',
    placeholder: 'sk-ant-...',
    keyRequired: true,
    supportsTools: true,
  },
  {
    id: 'google',
    name: 'Google AI',
    detail: 'Gemini models',
    baseUrl: 'https://generativelanguage.googleapis.com/v1beta/openai',
    placeholder: 'AIza...',
    keyRequired: true,
    supportsTools: false,
  },
  {
    id: 'openrouter',
    name: 'OpenRouter',
    detail: 'Multi-provider routing',
    baseUrl: 'https://openrouter.ai/api/v1',
    placeholder: 'sk-or-...',
    keyRequired: true,
    supportsTools: true,
  },
  {
    id: 'custom',
    name: 'Custom API',
    detail: 'OpenAI-compatible',
    baseUrl: '',
    placeholder: 'Optional API key',
    keyRequired: false,
    supportsTools: '',
  },
];

const LOCAL_PRESETS = [
  {
    id: 'ollama',
    label: 'Ollama',
    name: 'Ollama (local)',
    url: 'http://localhost:11434/v1',
  },
  {
    id: 'lmstudio',
    label: 'LM Studio',
    name: 'LM Studio',
    url: 'http://localhost:1234/v1',
  },
  {
    id: 'custom-local',
    label: 'Custom',
    name: 'Local model server',
    url: 'http://localhost:8000/v1',
  },
];

const FEATURE_CARDS = [
  {
    title: 'Chat',
    body: 'Fast sessions with files, images, voice, and searchable history.',
    icon: 'chat',
  },
  {
    title: 'Agent',
    body: 'Switch modes when a request needs tools, web work, or multiple steps.',
    icon: 'agent',
  },
  {
    title: 'Deep Research',
    body: 'Plan, search, collect sources, and produce long-form reports.',
    icon: 'search',
  },
  {
    title: 'Library',
    body: 'Keep chats, documents, research, notes, and exports organized.',
    icon: 'library',
  },
  {
    title: 'Memory',
    body: 'Store durable context and keep control over what Odysseus remembers.',
    icon: 'memory',
  },
  {
    title: 'Compare',
    body: 'Run models side by side when quality, speed, or cost matters.',
    icon: 'compare',
  },
];

const ICONS = {
  boat: '<svg viewBox="0 0 32 32" aria-hidden="true"><path d="M16 4L16 22L6 22Z" fill="currentColor"/><path d="M16 8L16 22L24 22Z" fill="currentColor" opacity="0.55"/><path d="M4 24Q10 20 16 24Q22 28 28 24" stroke="currentColor" stroke-width="2.5" fill="none" stroke-linecap="round"/></svg>',
  spark: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M13 2L4 14h7l-1 8 9-12h-7l1-8Z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>',
  model: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="3" y="4" width="18" height="14" rx="2.5" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M8 21h8M12 18v3M7 9h.01M10 9h7M7 13h.01M10 13h5" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  palette: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3a9 9 0 0 0 0 18h1.5a2 2 0 0 0 1.4-3.4 1.7 1.7 0 0 1 1.2-2.9H18a6 6 0 0 0 0-12h-6Z" fill="none" stroke="currentColor" stroke-width="1.7"/><circle cx="8.2" cy="10.2" r="1" fill="currentColor"/><circle cx="11.5" cy="7.7" r="1" fill="currentColor"/><circle cx="14.9" cy="10.3" r="1" fill="currentColor"/></svg>',
  check: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 6 9 17l-5-5" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  arrowRight: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 12h14M13 6l6 6-6 6" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  arrowLeft: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M19 12H5M11 6l-6 6 6 6" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  refresh: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M20 12a8 8 0 0 1-14 5.3M4 12a8 8 0 0 1 14-5.3M18 3v4h-4M6 21v-4h4" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  cloud: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M7 18h10a4 4 0 0 0 .6-7.96A6 6 0 0 0 6.3 8.5 4.75 4.75 0 0 0 7 18Z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>',
  server: '<svg viewBox="0 0 24 24" aria-hidden="true"><rect x="4" y="4" width="16" height="6" rx="2" fill="none" stroke="currentColor" stroke-width="1.7"/><rect x="4" y="14" width="16" height="6" rx="2" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="M8 7h.01M8 17h.01M12 7h4M12 17h4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  chat: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M4 5.5A2.5 2.5 0 0 1 6.5 3h11A2.5 2.5 0 0 1 20 5.5v7a2.5 2.5 0 0 1-2.5 2.5H10l-5 5v-14.5Z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/></svg>',
  agent: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M12 3v4M12 17v4M5.6 5.6l2.8 2.8M15.6 15.6l2.8 2.8M3 12h4M17 12h4M5.6 18.4l2.8-2.8M15.6 8.4l2.8-2.8" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="12" cy="12" r="3" fill="none" stroke="currentColor" stroke-width="1.7"/></svg>',
  search: '<svg viewBox="0 0 24 24" aria-hidden="true"><circle cx="11" cy="11" r="7" fill="none" stroke="currentColor" stroke-width="1.7"/><path d="m16 16 4 4" fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round"/></svg>',
  library: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M5 4h5v16H5zM10 4h5v16h-5zM16 6l4 1-3 13-4-1 3-13Z" fill="none" stroke="currentColor" stroke-width="1.5" stroke-linejoin="round"/></svg>',
  memory: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 17.5A7 7 0 1 1 17.8 8a5.2 5.2 0 0 1-.8 9.8V21H8v-3.5Z" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"/><path d="M9 10h6M9 13h4" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/></svg>',
  compare: '<svg viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5h12M8 12h12M8 19h12" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round"/><circle cx="4" cy="5" r="1.5" fill="currentColor"/><circle cx="4" cy="12" r="1.5" fill="currentColor"/><circle cx="4" cy="19" r="1.5" fill="currentColor"/></svg>',
};

let overlay = null;
let currentStep = 0;
let selectedProviderId = 'openai';
let selectedTheme = getSavedThemeName();
let selectedDensity = getSavedDensity();
let modelCatalog = emptyCatalog();
let selectedExistingModel = null;
let previousFocus = null;
let existingModelsLoaded = false;
let scaleListenersAttached = false;

function emptyCatalog() {
  return { endpoints: [], models: [] };
}

function isComplete(value) {
  return value === true || value === 'true' || value === 1 || value === '1' ||
    Boolean(value && typeof value === 'object' && value.complete);
}

async function readError(res) {
  try {
    const data = await res.json();
    if (data && data.detail) return String(data.detail);
    if (data && data.error) return String(data.error);
  } catch (_) {}
  try {
    const text = await res.text();
    if (text) return text.slice(0, 180);
  } catch (_) {}
  return `HTTP ${res.status}`;
}

async function getPref(key) {
  try {
    const res = await fetch(`${API_BASE}/api/prefs/${encodeURIComponent(key)}`, {
      credentials: 'same-origin',
      headers: { Accept: 'application/json' },
    });
    if (!res.ok) return null;
    const data = await res.json();
    return data.value;
  } catch (_) {
    return null;
  }
}

async function setPref(key, value) {
  await fetch(`${API_BASE}/api/prefs/${encodeURIComponent(key)}`, {
    method: 'PUT',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify({ value }),
  }).catch(() => {});
}

function parseModelCatalog(data) {
  const items = Array.isArray(data?.items) ? data.items : (Array.isArray(data) ? data : []);
  const models = [];

  for (const item of items) {
    const endpointId = item.endpoint_id || '';
    const endpointName = item.endpoint_name || item.host || 'Endpoint';
    const url = item.url || '';
    const curated = Array.isArray(item.models) ? item.models : [];
    const curatedDisplay = Array.isArray(item.models_display) ? item.models_display : [];
    const extra = Array.isArray(item.models_extra) ? item.models_extra : [];
    const extraDisplay = Array.isArray(item.models_extra_display) ? item.models_extra_display : [];

    curated.forEach((model, index) => {
      if (!model) return;
      models.push({
        model,
        displayName: curatedDisplay[index] || model,
        endpointId,
        endpointName,
        url,
      });
    });

    extra.forEach((model, index) => {
      if (!model) return;
      models.push({
        model,
        displayName: extraDisplay[index] || model,
        endpointId,
        endpointName,
        url,
      });
    });
  }

  const seen = new Set();
  const uniqueModels = models.filter((entry) => {
    const key = `${entry.endpointId}:${entry.model}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });

  return { endpoints: items, models: uniqueModels };
}

async function fetchModelCatalog(refresh = false) {
  const res = await fetch(`${API_BASE}/api/models${refresh ? '?refresh=true' : ''}`, {
    credentials: 'same-origin',
    headers: { Accept: 'application/json' },
  });
  if (!res.ok) throw new Error(await readError(res));
  return parseModelCatalog(await res.json());
}

function getSavedTheme() {
  try {
    return themeModule.getSaved?.() || null;
  } catch (_) {
    return null;
  }
}

function getSavedThemeName() {
  const saved = getSavedTheme();
  if (saved?.name && THEMES[saved.name]) return saved.name;
  return DEFAULT_THEME;
}

function getSavedDensity() {
  const saved = getSavedTheme();
  return saved?.density || DEFAULT_DENSITY;
}

function getThemeLabel(name) {
  if (name === 'gpt') return 'GPT';
  return String(name || '').replace(/[-_]/g, ' ').replace(/\b\w/g, (c) => c.toUpperCase());
}

function html(text) {
  return String(text ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

function renderProviderCard(provider) {
  return `
    <button class="onboarding-provider${provider.id === selectedProviderId ? ' is-selected' : ''}" type="button" data-provider-id="${provider.id}">
      <span class="onboarding-provider-icon">${provider.id === 'custom' ? ICONS.server : ICONS.cloud}</span>
      <span>
        <strong>${html(provider.name)}</strong>
        <small>${html(provider.detail)}</small>
      </span>
    </button>
  `;
}

function renderThemeSwatch(themeName) {
  const theme = THEMES[themeName];
  if (!theme) return '';
  const active = themeName === selectedTheme ? ' is-selected' : '';
  return `
    <button class="onboarding-theme-swatch${active}" type="button" data-theme-id="${html(themeName)}">
      <span class="onboarding-swatch-frame" aria-hidden="true" style="--ob-bg:${html(theme.bg)};--ob-panel:${html(theme.panel)};--ob-fg:${html(theme.fg)};--ob-accent:${html(theme.red)}">
        <span></span><span></span><span></span>
      </span>
      <span>${html(getThemeLabel(themeName))}</span>
    </button>
  `;
}

function visibleThemeNames() {
  const names = [selectedTheme, ...FEATURED_THEMES]
    .filter((name, index, all) => name && THEMES[name] && all.indexOf(name) === index);
  return names.slice(0, 8);
}

function renderDensityButton(option) {
  return `
    <button class="onboarding-density-btn${option.id === selectedDensity ? ' is-selected' : ''}" type="button" data-density="${option.id}">
      ${html(option.label)}
    </button>
  `;
}

function renderFeatureCard(feature) {
  return `
    <article class="onboarding-feature-card">
      <div class="onboarding-feature-icon">${ICONS[feature.icon] || ICONS.spark}</div>
      <div>
        <h3>${html(feature.title)}</h3>
        <p>${html(feature.body)}</p>
      </div>
    </article>
  `;
}

function buildOverlay() {
  const el = document.createElement('div');
  el.id = 'onboarding-overlay';
  el.className = 'onboarding-overlay';
  el.setAttribute('role', 'dialog');
  el.setAttribute('aria-modal', 'true');
  el.setAttribute('aria-labelledby', 'onboarding-title-0');

  el.innerHTML = `
    <div class="onboarding-scrim" aria-hidden="true"></div>
    <div class="onboarding-shell" role="document">
      <aside class="onboarding-aside">
        <div class="onboarding-brand">
          <div class="onboarding-brand-mark">${ICONS.boat}</div>
          <div>
            <strong>Odysseus</strong>
            <span>First run</span>
          </div>
        </div>

        <nav class="onboarding-step-list" aria-label="Setup progress">
          <button class="onboarding-step-link is-active" type="button" data-step-link="0">
            <span>01</span><strong>Welcome</strong><small>Start clean</small>
          </button>
          <button class="onboarding-step-link" type="button" data-step-link="1">
            <span>02</span><strong>Model</strong><small>Connect intelligence</small>
          </button>
          <button class="onboarding-step-link" type="button" data-step-link="2">
            <span>03</span><strong>Interface</strong><small>Tune the feel</small>
          </button>
          <button class="onboarding-step-link" type="button" data-step-link="3">
            <span>04</span><strong>Features</strong><small>Know the map</small>
          </button>
        </nav>

        <button class="onboarding-skip" type="button" data-onboarding-skip>Skip setup</button>
      </aside>

      <section class="onboarding-main">
        <div class="onboarding-progress" aria-hidden="true">
          <span class="is-active"></span><span></span><span></span><span></span>
        </div>

        <article class="onboarding-step is-active" data-step="0">
          <div class="onboarding-kicker">${ICONS.spark}<span>Private AI workspace</span></div>
          <h1 class="onboarding-title" id="onboarding-title-0" tabindex="-1">Welcome to Odysseus</h1>
          <p class="onboarding-lede">Set the model, tune the interface, and get a quick map of the workspace. Four focused steps, then the dashboard stays out of your way.</p>
          <div class="onboarding-value-grid">
            <div><strong>Self-hosted</strong><span>Your data stays with your server.</span></div>
            <div><strong>Model-flexible</strong><span>Use cloud APIs, local models, or both.</span></div>
            <div><strong>Built for work</strong><span>Chat, research, files, memory, and tools.</span></div>
          </div>
          <div class="onboarding-actions">
            <button class="onboarding-primary" type="button" data-onboarding-next>Get started ${ICONS.arrowRight}</button>
          </div>
        </article>

        <article class="onboarding-step" data-step="1">
          <div class="onboarding-kicker">${ICONS.model}<span>Model setup</span></div>
          <h2 class="onboarding-title" id="onboarding-title-1" tabindex="-1">Connect a model</h2>
          <p class="onboarding-lede">Add a provider now, point Odysseus at a local server, or keep the model setup that is already available.</p>

          <div class="onboarding-model-summary" id="onboarding-model-summary">
            <span class="onboarding-spinner" aria-hidden="true"></span>
            <span>Checking configured models...</span>
          </div>

          <div class="onboarding-tabs" role="tablist" aria-label="Model setup options">
            <button class="is-active" type="button" role="tab" aria-selected="true" data-model-tab="cloud">Cloud API</button>
            <button type="button" role="tab" aria-selected="false" data-model-tab="local">Local endpoint</button>
            <button type="button" role="tab" aria-selected="false" data-model-tab="existing">Current setup</button>
          </div>

          <div class="onboarding-tab-panel is-active" data-model-panel="cloud">
            <div class="onboarding-provider-grid">
              ${CLOUD_PROVIDERS.map(renderProviderCard).join('')}
            </div>
            <div class="onboarding-form-grid">
              <label>
                <span>Endpoint name</span>
                <input class="onboarding-input" id="onboarding-cloud-name" autocomplete="off">
              </label>
              <label>
                <span>Base URL</span>
                <input class="onboarding-input" id="onboarding-cloud-url" autocomplete="off" spellcheck="false">
              </label>
              <label class="onboarding-form-wide">
                <span>API key</span>
                <input class="onboarding-input" id="onboarding-cloud-key" type="password" autocomplete="off" spellcheck="false">
              </label>
            </div>
            <label class="onboarding-check">
              <input id="onboarding-cloud-default" type="checkbox" checked>
              <span>Make the first discovered chat model my default.</span>
            </label>
            <div class="onboarding-inline-actions">
              <button class="onboarding-primary" id="onboarding-cloud-save" type="button">Save endpoint ${ICONS.check}</button>
              <div class="onboarding-status" id="onboarding-cloud-status" role="status"></div>
            </div>
          </div>

          <div class="onboarding-tab-panel" data-model-panel="local">
            <div class="onboarding-local-presets">
              ${LOCAL_PRESETS.map((preset) => `
                <button type="button" data-local-preset="${preset.id}">
                  ${ICONS.server}<span>${html(preset.label)}</span>
                </button>
              `).join('')}
            </div>
            <div class="onboarding-form-grid">
              <label>
                <span>Endpoint name</span>
                <input class="onboarding-input" id="onboarding-local-name" autocomplete="off">
              </label>
              <label>
                <span>Base URL</span>
                <input class="onboarding-input" id="onboarding-local-url" autocomplete="off" spellcheck="false">
              </label>
            </div>
            <label class="onboarding-check">
              <input id="onboarding-local-default" type="checkbox" checked>
              <span>Make the first discovered chat model my default.</span>
            </label>
            <div class="onboarding-inline-actions">
              <button class="onboarding-primary" id="onboarding-local-save" type="button">Save local endpoint ${ICONS.check}</button>
              <div class="onboarding-status" id="onboarding-local-status" role="status"></div>
            </div>
          </div>

          <div class="onboarding-tab-panel" data-model-panel="existing">
            <div class="onboarding-existing-head">
              <div>
                <strong>Available models</strong>
                <span id="onboarding-existing-count">Checking...</span>
              </div>
              <button class="onboarding-icon-btn" id="onboarding-refresh-models" type="button" title="Refresh models" aria-label="Refresh models">${ICONS.refresh}</button>
            </div>
            <div class="onboarding-model-list" id="onboarding-existing-models"></div>
            <div class="onboarding-inline-actions">
              <button class="onboarding-secondary" id="onboarding-use-first-model" type="button">Use first available model</button>
              <div class="onboarding-status" id="onboarding-existing-status" role="status"></div>
            </div>
          </div>

          <div class="onboarding-actions">
            <button class="onboarding-secondary" type="button" data-onboarding-back>${ICONS.arrowLeft} Back</button>
            <button class="onboarding-primary" type="button" data-onboarding-next>Continue ${ICONS.arrowRight}</button>
          </div>
        </article>

        <article class="onboarding-step" data-step="2">
          <div class="onboarding-kicker">${ICONS.palette}<span>Application feel</span></div>
          <h2 class="onboarding-title" id="onboarding-title-2" tabindex="-1">Make it yours</h2>
          <p class="onboarding-lede">Pick a visual baseline and density. This saves into the same theme system as the rest of the app.</p>

          <div class="onboarding-section-label">Theme</div>
          <div class="onboarding-theme-grid">
            ${visibleThemeNames().map(renderThemeSwatch).join('')}
          </div>

          <div class="onboarding-section-label">Density</div>
          <div class="onboarding-density-group">
            ${DENSITY_OPTIONS.map(renderDensityButton).join('')}
          </div>

          <div class="onboarding-actions">
            <button class="onboarding-secondary" type="button" data-onboarding-back>${ICONS.arrowLeft} Back</button>
            <button class="onboarding-primary" type="button" data-onboarding-next>Continue ${ICONS.arrowRight}</button>
          </div>
        </article>

        <article class="onboarding-step" data-step="3">
          <div class="onboarding-kicker">${ICONS.spark}<span>Workspace map</span></div>
          <h2 class="onboarding-title" id="onboarding-title-3" tabindex="-1">You are ready</h2>
          <p class="onboarding-lede">The dashboard opens into chat, but the sidebar is where Odysseus becomes a full workspace.</p>

          <div class="onboarding-feature-grid">
            ${FEATURE_CARDS.map(renderFeatureCard).join('')}
          </div>

          <div class="onboarding-actions">
            <button class="onboarding-secondary" type="button" data-onboarding-back>${ICONS.arrowLeft} Back</button>
            <button class="onboarding-primary" type="button" data-onboarding-finish>Start using Odysseus ${ICONS.arrowRight}</button>
          </div>
        </article>
      </section>
    </div>
  `;

  return el;
}

function setStatus(id, type, message) {
  const el = overlay?.querySelector(`#${id}`);
  if (!el) return;
  el.className = `onboarding-status${type ? ` is-${type}` : ''}`;
  el.textContent = message || '';
}

function setButtonBusy(button, busy, label) {
  if (!button) return;
  if (busy) {
    button.dataset.originalHtml = button.innerHTML;
    button.disabled = true;
    button.textContent = label || 'Working...';
    return;
  }
  button.disabled = false;
  if (button.dataset.originalHtml) {
    button.innerHTML = button.dataset.originalHtml;
    delete button.dataset.originalHtml;
  }
}

function selectProvider(providerId) {
  const provider = CLOUD_PROVIDERS.find((item) => item.id === providerId) || CLOUD_PROVIDERS[0];
  selectedProviderId = provider.id;

  overlay?.querySelectorAll('.onboarding-provider').forEach((button) => {
    const selected = button.dataset.providerId === provider.id;
    button.classList.toggle('is-selected', selected);
    button.setAttribute('aria-pressed', selected ? 'true' : 'false');
  });

  const name = overlay?.querySelector('#onboarding-cloud-name');
  const url = overlay?.querySelector('#onboarding-cloud-url');
  const key = overlay?.querySelector('#onboarding-cloud-key');
  const save = overlay?.querySelector('#onboarding-cloud-save');

  if (name) name.value = provider.id === 'custom' ? 'Custom API' : provider.name;
  if (url) {
    url.value = provider.baseUrl;
    url.placeholder = provider.id === 'custom' ? 'https://api.example.com/v1' : provider.baseUrl;
  }
  if (key) {
    key.value = '';
    key.placeholder = provider.placeholder;
  }
  if (save && !save.disabled) {
    save.innerHTML = `Save ${html(provider.name)} endpoint ${ICONS.check}`;
    delete save.dataset.originalHtml;
  }
  setStatus(
    'onboarding-cloud-status',
    'info',
    `${provider.name} selected. ${provider.keyRequired ? 'Paste the API key, then save the endpoint.' : 'Confirm the URL, then save the endpoint.'}`
  );
}

function selectLocalPreset(presetId) {
  const preset = LOCAL_PRESETS.find((item) => item.id === presetId) || LOCAL_PRESETS[0];
  overlay?.querySelectorAll('[data-local-preset]').forEach((button) => {
    button.classList.toggle('is-selected', button.dataset.localPreset === preset.id);
  });
  const name = overlay?.querySelector('#onboarding-local-name');
  const url = overlay?.querySelector('#onboarding-local-url');
  if (name) name.value = preset.name;
  if (url) url.value = preset.url;
}

function setModelTab(tab) {
  overlay?.querySelectorAll('[data-model-tab]').forEach((button) => {
    const active = button.dataset.modelTab === tab;
    button.classList.toggle('is-active', active);
    button.setAttribute('aria-selected', active ? 'true' : 'false');
  });
  overlay?.querySelectorAll('[data-model-panel]').forEach((panel) => {
    panel.classList.toggle('is-active', panel.dataset.modelPanel === tab);
  });
  if (tab === 'existing') refreshExistingModels(false);
}

function renderExistingModels() {
  const summary = overlay?.querySelector('#onboarding-model-summary');
  const count = overlay?.querySelector('#onboarding-existing-count');
  const list = overlay?.querySelector('#onboarding-existing-models');
  const useFirst = overlay?.querySelector('#onboarding-use-first-model');

  const endpointCount = modelCatalog.endpoints.length;
  const modelCount = modelCatalog.models.length;

  if (summary) {
    summary.innerHTML = modelCount
      ? `${ICONS.check}<span>${modelCount} model${modelCount === 1 ? '' : 's'} available across ${endpointCount || 1} endpoint${endpointCount === 1 ? '' : 's'}.</span>`
      : `${ICONS.model}<span>No chat model is available yet. Add one here or continue and configure it later.</span>`;
    summary.classList.toggle('is-ready', modelCount > 0);
  }

  if (count) {
    count.textContent = modelCount
      ? `${modelCount} model${modelCount === 1 ? '' : 's'} found`
      : 'No models found';
  }

  if (useFirst) useFirst.disabled = !modelCount;

  if (!list) return;
  if (!modelCount) {
    list.innerHTML = '<div class="onboarding-empty">No models are visible from this account yet.</div>';
    return;
  }

  list.innerHTML = modelCatalog.models.slice(0, 10).map((entry) => {
    const selected = selectedExistingModel
      && selectedExistingModel.endpointId === entry.endpointId
      && selectedExistingModel.model === entry.model;
    return `
    <div class="onboarding-model-chip${selected ? ' is-selected' : ''}" role="button" tabindex="0"
      data-model-pick data-endpoint-id="${html(entry.endpointId)}" data-model="${html(entry.model)}"
      data-display="${html(entry.displayName || entry.model)}" aria-pressed="${selected ? 'true' : 'false'}">
      <strong>${html(entry.displayName || entry.model)}</strong>
      <span>${html(entry.endpointName || 'Endpoint')}</span>
    </div>`;
  }).join('') + (modelCount > 10 ? `<div class="onboarding-model-chip is-more">+${modelCount - 10} more</div>` : '');
}

async function selectExistingModel(endpointId, model, displayName) {
  if (!endpointId || !model) return;
  const statusId = 'onboarding-existing-status';
  selectedExistingModel = { endpointId, model };
  renderExistingModels();
  setStatus(statusId, '', 'Setting default...');
  try {
    await saveDefaultModel(endpointId, model);
    setStatus(statusId, 'success', `Default model: ${displayName || model}`);
  } catch (err) {
    selectedExistingModel = null;
    renderExistingModels();
    setStatus(statusId, 'error', String(err?.message || err || 'Could not set default model.'));
  }
}

async function refreshExistingModels(force = false) {
  if (existingModelsLoaded && !force) {
    renderExistingModels();
    return;
  }
  const list = overlay?.querySelector('#onboarding-existing-models');
  if (list) list.innerHTML = '<div class="onboarding-empty">Checking models...</div>';
  try {
    modelCatalog = await fetchModelCatalog(force);
    existingModelsLoaded = true;
  } catch (_) {
    modelCatalog = emptyCatalog();
  }
  renderExistingModels();
}

function firstModelFromEndpointResult(result) {
  const models = Array.isArray(result?.models) ? result.models : [];
  return models.find((model) => model && !/embedding|whisper|tts|image|dall-e/i.test(model)) || models[0] || '';
}

async function createEndpoint({ name, baseUrl, apiKey, shared = false, supportsTools = '' }) {
  const form = new FormData();
  form.set('name', name);
  form.set('base_url', baseUrl);
  form.set('api_key', apiKey || '');
  form.set('skip_probe', 'false');
  form.set('require_models', 'false');
  form.set('model_type', 'llm');
  form.set('shared', shared ? 'true' : 'false');
  if (supportsTools !== '') form.set('supports_tools', supportsTools ? 'true' : 'false');

  const res = await fetch(`${API_BASE}/api/model-endpoints`, {
    method: 'POST',
    credentials: 'same-origin',
    body: form,
  });
  if (!res.ok) throw new Error(await readError(res));
  return res.json();
}

async function saveDefaultModel(endpointId, model) {
  if (!endpointId || !model) return false;

  const payload = {
    default_endpoint_id: endpointId,
    default_model: model,
    default_model_fallbacks: [],
  };

  const adminRes = await fetch(`${API_BASE}/api/auth/settings`, {
    method: 'POST',
    credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json', Accept: 'application/json' },
    body: JSON.stringify(payload),
  });

  if (adminRes.ok) return true;

  if (adminRes.status !== 403) {
    throw new Error(await readError(adminRes));
  }

  await Promise.all([
    setPref('default_endpoint_id', endpointId),
    setPref('default_model', model),
    setPref('default_model_fallbacks', []),
  ]);
  return true;
}

async function saveCloudEndpoint() {
  const provider = CLOUD_PROVIDERS.find((item) => item.id === selectedProviderId) || CLOUD_PROVIDERS[0];
  const name = overlay?.querySelector('#onboarding-cloud-name')?.value.trim() || provider.name;
  const baseUrl = overlay?.querySelector('#onboarding-cloud-url')?.value.trim() || provider.baseUrl;
  const apiKey = overlay?.querySelector('#onboarding-cloud-key')?.value.trim() || '';
  const setDefault = overlay?.querySelector('#onboarding-cloud-default')?.checked !== false;
  const button = overlay?.querySelector('#onboarding-cloud-save');

  if (!baseUrl) {
    setStatus('onboarding-cloud-status', 'error', 'Enter the provider base URL.');
    return;
  }
  if (provider.keyRequired && !apiKey) {
    setStatus('onboarding-cloud-status', 'error', 'Enter the API key for this provider.');
    return;
  }

  setButtonBusy(button, true, 'Saving...');
  setStatus('onboarding-cloud-status', '', '');

  try {
    const result = await createEndpoint({
      name,
      baseUrl,
      apiKey,
      shared: false,
      supportsTools: provider.supportsTools,
    });
    const model = firstModelFromEndpointResult(result);
    if (setDefault && result.id && model) await saveDefaultModel(result.id, model);
    existingModelsLoaded = false;
    await refreshExistingModels(true);
    const keyInput = overlay?.querySelector('#onboarding-cloud-key');
    if (keyInput) keyInput.value = '';
    setStatus(
      'onboarding-cloud-status',
      'success',
      model && setDefault ? `Saved. Default model: ${model}` : 'Endpoint saved. Models will appear after refresh.'
    );
  } catch (err) {
    const message = String(err?.message || err || 'Save failed');
    setStatus(
      'onboarding-cloud-status',
      'error',
      /admin/i.test(message) ? 'Admin access is required to add model endpoints.' : message
    );
  } finally {
    setButtonBusy(button, false);
  }
}

async function saveLocalEndpoint() {
  const name = overlay?.querySelector('#onboarding-local-name')?.value.trim() || 'Local model server';
  const baseUrl = overlay?.querySelector('#onboarding-local-url')?.value.trim() || '';
  const setDefault = overlay?.querySelector('#onboarding-local-default')?.checked !== false;
  const button = overlay?.querySelector('#onboarding-local-save');

  if (!baseUrl) {
    setStatus('onboarding-local-status', 'error', 'Enter the local endpoint URL.');
    return;
  }

  setButtonBusy(button, true, 'Saving...');
  setStatus('onboarding-local-status', '', '');

  try {
    const result = await createEndpoint({ name, baseUrl, apiKey: '', shared: false, supportsTools: '' });
    const model = firstModelFromEndpointResult(result);
    if (setDefault && result.id && model) await saveDefaultModel(result.id, model);
    existingModelsLoaded = false;
    await refreshExistingModels(true);
    setStatus(
      'onboarding-local-status',
      'success',
      model && setDefault ? `Saved. Default model: ${model}` : 'Endpoint saved. Start the server, then refresh models.'
    );
  } catch (err) {
    const message = String(err?.message || err || 'Save failed');
    setStatus(
      'onboarding-local-status',
      'error',
      /admin/i.test(message) ? 'Admin access is required to add model endpoints.' : message
    );
  } finally {
    setButtonBusy(button, false);
  }
}

async function useFirstAvailableModel() {
  const statusId = 'onboarding-existing-status';
  const button = overlay?.querySelector('#onboarding-use-first-model');
  const entry = modelCatalog.models[0];
  if (!entry?.endpointId || !entry.model) {
    setStatus(statusId, 'error', 'No configured model is available yet.');
    return;
  }

  setButtonBusy(button, true, 'Saving...');
  setStatus(statusId, '', '');

  try {
    await saveDefaultModel(entry.endpointId, entry.model);
    selectedExistingModel = { endpointId: entry.endpointId, model: entry.model };
    renderExistingModels();
    setStatus(statusId, 'success', `Default model: ${entry.displayName || entry.model}`);
  } catch (err) {
    setStatus(statusId, 'error', String(err?.message || err || 'Could not set default model.'));
  } finally {
    setButtonBusy(button, false);
  }
}

function applySelectedTheme(themeName) {
  const colors = THEMES[themeName];
  if (!colors) return;

  selectedTheme = themeName;

  const bgPattern = THEME_DEFAULT_PATTERN[themeName] || 'none';
  const effectColor = THEME_DEFAULT_EFFECT_COLOR[themeName] || '';
  const effectIntensity = THEME_DEFAULT_INTENSITY[themeName] ?? 1;

  themeModule.applyColors(colors);
  themeModule.applyFontDensity(DEFAULT_FONT, selectedDensity);
  themeModule.applyBgEffectColor?.(effectColor);
  themeModule.applyBgEffectIntensity?.(effectIntensity);
  themeModule.applyBgEffectSize?.(1);
  themeModule.applyFrostedGlass?.(false);
  themeModule.applyBgPattern(bgPattern);
  themeModule.save(themeName, colors, {
    font: DEFAULT_FONT,
    density: selectedDensity,
    bgPattern,
    bgEffectColor: effectColor,
    bgEffectIntensity: effectIntensity,
    bgEffectSize: 1,
    frosted: false,
  });

  overlay?.querySelectorAll('.onboarding-theme-swatch').forEach((button) => {
    button.classList.toggle('is-selected', button.dataset.themeId === themeName);
  });
  if (overlay) overlay.dataset.density = selectedDensity;
}

function applySelectedDensity(density) {
  selectedDensity = density || DEFAULT_DENSITY;
  const colors = THEMES[selectedTheme] || THEMES[DEFAULT_THEME];
  const bgPattern = THEME_DEFAULT_PATTERN[selectedTheme] || 'none';
  const effectColor = THEME_DEFAULT_EFFECT_COLOR[selectedTheme] || '';
  const effectIntensity = THEME_DEFAULT_INTENSITY[selectedTheme] ?? 1;

  themeModule.applyFontDensity(DEFAULT_FONT, selectedDensity);
  themeModule.save(selectedTheme, colors, {
    font: DEFAULT_FONT,
    density: selectedDensity,
    bgPattern,
    bgEffectColor: effectColor,
    bgEffectIntensity: effectIntensity,
    bgEffectSize: 1,
    frosted: false,
  });

  overlay?.querySelectorAll('.onboarding-density-btn').forEach((button) => {
    button.classList.toggle('is-selected', button.dataset.density === selectedDensity);
  });
  if (overlay) overlay.dataset.density = selectedDensity;
  syncOnboardingScale();
}

function syncOnboardingScale() {
  if (!overlay) return;
  if (window.matchMedia('(max-width: 760px)').matches) {
    overlay.style.setProperty('--onboarding-scale', '1');
    return;
  }

  const styles = getComputedStyle(overlay);
  const designWidth = parseFloat(styles.getPropertyValue('--onboarding-design-width')) || 1040;
  const designHeight = parseFloat(styles.getPropertyValue('--onboarding-design-height')) || 720;
  const viewport = window.visualViewport || window;
  const viewportWidth = Math.max(320, (viewport.width || window.innerWidth) - 32);
  const viewportHeight = Math.max(320, (viewport.height || window.innerHeight) - 32);
  const scale = Math.min(1, viewportWidth / designWidth, viewportHeight / designHeight);

  overlay.style.setProperty('--onboarding-scale', Math.max(0.5, scale).toFixed(3));
}

function attachScaleListeners() {
  if (scaleListenersAttached) return;
  scaleListenersAttached = true;
  window.addEventListener('resize', syncOnboardingScale, { passive: true });
  window.visualViewport?.addEventListener('resize', syncOnboardingScale, { passive: true });
}

function detachScaleListeners() {
  if (!scaleListenersAttached) return;
  scaleListenersAttached = false;
  window.removeEventListener('resize', syncOnboardingScale);
  window.visualViewport?.removeEventListener('resize', syncOnboardingScale);
}

function goToStep(step) {
  currentStep = Math.max(0, Math.min(TOTAL_STEPS - 1, step));

  overlay?.querySelectorAll('.onboarding-step').forEach((panel) => {
    panel.classList.toggle('is-active', Number(panel.dataset.step) === currentStep);
  });
  overlay?.querySelectorAll('.onboarding-step-link').forEach((link) => {
    const linkStep = Number(link.dataset.stepLink);
    link.classList.toggle('is-active', linkStep === currentStep);
    link.classList.toggle('is-complete', linkStep < currentStep);
  });
  overlay?.querySelectorAll('.onboarding-progress span').forEach((dot, index) => {
    dot.classList.toggle('is-active', index === currentStep);
    dot.classList.toggle('is-complete', index < currentStep);
  });

  if (currentStep === 1) refreshExistingModels(false);

  requestAnimationFrame(() => {
    const title = overlay?.querySelector(`#onboarding-title-${currentStep}`);
    if (title) title.focus({ preventScroll: true });
  });
}

function focusableElements() {
  return Array.from(overlay?.querySelectorAll(
    'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])'
  ) || []).filter((node) => node.offsetParent !== null);
}

function handleKeydown(event) {
  if (event.key === 'Escape') {
    event.preventDefault();
    dismiss();
    return;
  }

  if (event.key !== 'Tab') return;
  const nodes = focusableElements();
  if (!nodes.length) return;
  const first = nodes[0];
  const last = nodes[nodes.length - 1];

  if (event.shiftKey && document.activeElement === first) {
    event.preventDefault();
    last.focus();
  } else if (!event.shiftKey && document.activeElement === last) {
    event.preventDefault();
    first.focus();
  }
}

function wireOverlay() {
  overlay.querySelectorAll('[data-onboarding-next]').forEach((button) => {
    button.addEventListener('click', () => goToStep(currentStep + 1));
  });
  overlay.querySelectorAll('[data-onboarding-back]').forEach((button) => {
    button.addEventListener('click', () => goToStep(currentStep - 1));
  });
  overlay.querySelector('[data-onboarding-finish]')?.addEventListener('click', dismiss);
  overlay.querySelector('[data-onboarding-skip]')?.addEventListener('click', dismiss);
  overlay.querySelectorAll('[data-step-link]').forEach((button) => {
    button.addEventListener('click', () => goToStep(Number(button.dataset.stepLink) || 0));
  });
  overlay.querySelectorAll('[data-model-tab]').forEach((button) => {
    button.addEventListener('click', () => setModelTab(button.dataset.modelTab));
  });
  overlay.querySelectorAll('[data-provider-id]').forEach((button) => {
    button.addEventListener('click', () => selectProvider(button.dataset.providerId));
  });
  overlay.querySelectorAll('[data-local-preset]').forEach((button) => {
    button.addEventListener('click', () => selectLocalPreset(button.dataset.localPreset));
  });
  overlay.querySelector('#onboarding-cloud-save')?.addEventListener('click', saveCloudEndpoint);
  overlay.querySelector('#onboarding-local-save')?.addEventListener('click', saveLocalEndpoint);
  overlay.querySelector('#onboarding-refresh-models')?.addEventListener('click', () => refreshExistingModels(true));
  overlay.querySelector('#onboarding-use-first-model')?.addEventListener('click', useFirstAvailableModel);
  const modelList = overlay.querySelector('#onboarding-existing-models');
  modelList?.addEventListener('click', (event) => {
    const chip = event.target.closest('[data-model-pick]');
    if (!chip) return;
    selectExistingModel(chip.dataset.endpointId, chip.dataset.model, chip.dataset.display);
  });
  modelList?.addEventListener('keydown', (event) => {
    if (event.key !== 'Enter' && event.key !== ' ' && event.key !== 'Spacebar') return;
    const chip = event.target.closest('[data-model-pick]');
    if (!chip) return;
    event.preventDefault();
    selectExistingModel(chip.dataset.endpointId, chip.dataset.model, chip.dataset.display);
  });
  overlay.querySelectorAll('[data-theme-id]').forEach((button) => {
    button.addEventListener('click', () => applySelectedTheme(button.dataset.themeId));
  });
  overlay.querySelectorAll('[data-density]').forEach((button) => {
    button.addEventListener('click', () => applySelectedDensity(button.dataset.density));
  });
  overlay.addEventListener('keydown', handleKeydown);

  selectProvider(selectedProviderId);
  selectLocalPreset('ollama');
  renderExistingModels();
}

function cleanupOverlay() {
  if (!overlay) return;
  detachScaleListeners();
  overlay.remove();
  overlay = null;
  document.body.classList.remove('onboarding-open');
  if (previousFocus && typeof previousFocus.focus === 'function') {
    try { previousFocus.focus({ preventScroll: true }); } catch (_) {}
  }
}

async function dismiss() {
  await setPref(PREF_KEY, true);
  if (!overlay) return;
  overlay.classList.add('is-leaving');
  window.setTimeout(cleanupOverlay, 180);
}

async function showOnboarding() {
  if (overlay) return;

  previousFocus = document.activeElement;
  existingModelsLoaded = false;
  selectedExistingModel = null;
  modelCatalog = await fetchModelCatalog(false).catch(() => emptyCatalog());
  selectedTheme = getSavedThemeName();
  selectedDensity = getSavedDensity();

  overlay = buildOverlay();
  overlay.dataset.density = selectedDensity;
  document.body.appendChild(overlay);
  document.body.classList.add('onboarding-open');
  attachScaleListeners();
  wireOverlay();
  syncOnboardingScale();
  goToStep(0);
}

export async function maybeShowOnboarding() {
  const done = await getPref(PREF_KEY);
  if (isComplete(done)) return;
  await showOnboarding();
}

export default { maybeShowOnboarding };
