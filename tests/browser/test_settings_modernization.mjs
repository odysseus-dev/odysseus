// Real-browser regression for the Settings finder, section deep links and
// configuration scope.
//
// Runs isolated Odysseus instances (temporary data directory and SQLite
// database, ephemeral ports, no live services) and drives them with Playwright:
//
//   node tests/browser/test_settings_modernization.mjs
//
// Optional: ARTIFACT_DIR=<dir> saves desktop/mobile and light/dark screenshots.

import { spawn, spawnSync, execFileSync } from 'child_process';
import { chromium } from 'playwright';
import { mkdirSync, mkdtempSync, rmSync, existsSync } from 'fs';
import { join, resolve } from 'path';
import { tmpdir, homedir } from 'os';
import { fileURLToPath } from 'url';
import net from 'net';

const REPO_ROOT = resolve(process.env.REPO_ROOT || join(fileURLToPath(import.meta.url), '..', '..', '..'));
const ARTIFACT_DIR = process.env.ARTIFACT_DIR || null;
const ADMIN = { username: 'settingsadmin', password: 'admin-password-123' };
const MEMBER = { username: 'settingsmember', password: 'member-password-123' };

const instances = [];
const forbiddenReport = {};
let browser = null;
const results = [];

const PROFILE = process.env.ODYSSEUS_PROFILE_SETTINGS === '1' || process.env.ODYSSEUS_PROFILE_BROWSER === '1';
const scenarioTimings = [];

async function timeScenario(name, fn) {
  if (!PROFILE) return fn();
  const t0 = performance.now();
  try {
    return await fn();
  } finally {
    const elapsed = Math.round(performance.now() - t0);
    scenarioTimings.push({ name, durationMs: elapsed });
    console.log(`[scenario-timing] ${name}: ${elapsed}ms`);
  }
}

function check(name, pass, detail = '') {
  results.push({ name, pass: Boolean(pass), detail });
  console.log(`${pass ? 'PASS' : 'FAIL'} ${name}${!pass && detail ? ` :: ${detail}` : ''}`);
}

function resolvePython() {
  const candidates = [
    process.env.PYTHON,
    process.env.ODYSSEUS_TEST_PYTHON,
    process.env.VIRTUAL_ENV ? join(process.env.VIRTUAL_ENV, 'bin', 'python') : null,
    join(REPO_ROOT, '..', '..', 'dev', 'venvs', 'main', 'bin', 'python'),
    join(REPO_ROOT, '..', '..', 'dev', 'venv', 'bin', 'python'),
    join(REPO_ROOT, '..', 'dev', 'venv', 'bin', 'python'),
    join(REPO_ROOT, 'venv', 'bin', 'python'),
    join(REPO_ROOT, '.venv', 'bin', 'python'),
    join(homedir(), '.cache', 'odysseus-ci-baseline-py311', 'bin', 'python'),
    'python3',
  ].filter(Boolean);
  for (const candidate of candidates) {
    try {
      if (spawnSync(candidate, ['-c', 'import uvicorn'], { stdio: 'ignore' }).status === 0) return candidate;
    } catch (_) {}
  }
  return 'python3';
}

function freePort() {
  return new Promise((resolvePort, reject) => {
    const server = net.createServer();
    server.unref();
    server.on('error', reject);
    server.listen(0, '127.0.0.1', () => {
      const { port } = server.address();
      server.close(() => resolvePort(port));
    });
  });
}

async function waitFor(url, timeoutMs = 40000, instance = null) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.status < 500) return;
    } catch (_) {}
    await new Promise(r => setTimeout(r, 100));
  }
  const extra = instance ? `\nServer output:\n${instance.log()}` : '';
  throw new Error(`Server did not start at ${url}${extra}`);
}

async function launchInstance(name, { authEnabled }) {
  const root = mkdtempSync(join(tmpdir(), `odysseus-settings-${name}-`));
  const dataDir = join(root, 'data');
  for (const dir of ['data', 'fastembed', 'mail', 'runtime']) mkdirSync(join(root, dir), { recursive: true });
  const dbPath = join(root, 'test.db');
  const port = await freePort();
  const chromaGuardPort = await freePort();

  const env = {
    ...process.env,
    ODYSSEUS_DATA_DIR: dataDir,
    DATABASE_URL: `sqlite:///${dbPath}`,
    AUTH_ENABLED: authEnabled ? 'true' : 'false',
    LOCALHOST_BYPASS: 'false',
    PORT: String(port),
    ODYSSEUS_TOOL_INDEX_PREWARM: '0',
    ODYSSEUS_STARTUP_WARMUPS: '0',
    ODYSSEUS_MODEL_KEEPALIVE: '0',
    // Settings tests need no MCP servers; this also keeps the instance from
    // fetching the browser MCP package from the network.
    ODYSSEUS_DISABLE_MCP: '1',
    CHROMADB_HOST: '127.0.0.1',
    CHROMADB_PORT: String(chromaGuardPort),
    FASTEMBED_CACHE_PATH: join(root, 'fastembed'),
    ODYSSEUS_MAIL_ATTACHMENTS_DIR: join(root, 'mail'),
    XDG_RUNTIME_DIR: join(root, 'runtime'),
    PYTHONPATH: REPO_ROOT,
    OPENAI_API_KEY: '',
    ANTHROPIC_API_KEY: '',
    SEARXNG_URL: '',
    NTFY_URL: '',
    OLLAMA_BASE_URL: 'http://127.0.0.1:0',
    OLLAMA_URL: 'http://127.0.0.1:0',
  };
  const proc = spawn(
    resolvePython(),
    ['-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', String(port), '--log-level', 'warning'],
    { env, cwd: REPO_ROOT, detached: process.platform !== 'win32', stdio: ['ignore', 'pipe', 'pipe'] },
  );
  let log = '';
  proc.stdout.on('data', d => { log += d; });
  proc.stderr.on('data', d => { log += d; });
  const instance = { name, root, dbPath, proc, baseUrl: `http://127.0.0.1:${port}`, log: () => log };
  instances.push(instance);
  await waitFor(`${instance.baseUrl}/api/health`, 40000, instance);
  return instance;
}

function stopAll() {
  if (browser) {
    try { browser.close(); } catch (_) {}
    browser = null;
  }
  for (const instance of instances.splice(0)) {
    try { process.kill(-instance.proc.pid, 'SIGKILL'); } catch (_) {
      try { instance.proc.kill('SIGKILL'); } catch (_) {}
    }
    if (existsSync(instance.root)) rmSync(instance.root, { recursive: true, force: true });
  }
}
process.on('exit', stopAll);
process.on('SIGINT', () => { stopAll(); process.exit(130); });

function seedEndpoints(instance) {
  // Shared (ownerless) chat endpoints with cached model lists, so model
  // pickers have deterministic choices without contacting any provider.
  const sql = `
    INSERT INTO model_endpoints (id, name, base_url, is_enabled, cached_models, model_type, endpoint_kind, created_at, updated_at)
    VALUES ('ep-alpha', 'Alpha Local', 'http://127.0.0.1:9/v1', 1, '["alpha-small","alpha-large"]', 'llm', 'auto', datetime('now'), datetime('now'));
    INSERT INTO model_endpoints (id, name, base_url, is_enabled, cached_models, model_type, endpoint_kind, created_at, updated_at)
    VALUES ('ep-beta', 'Beta Local', 'http://127.0.0.1:9/v1', 1, '["beta-chat"]', 'llm', 'auto', datetime('now'), datetime('now'));
  `;
  // Python's sqlite3 module, not the sqlite3 command-line tool, which CI does
  // not guarantee.
  execFileSync(resolvePython(), ['-c', 'import sqlite3, sys; c = sqlite3.connect(sys.argv[1]); c.executescript(sys.stdin.read()); c.commit()', instance.dbPath], { input: sql });
}

async function apiLogin(instance, user) {
  const response = await fetch(`${instance.baseUrl}/api/auth/login`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ ...user, remember: true }),
  });
  if (!response.ok) throw new Error(`login ${user.username} failed: ${response.status}`);
  const cookie = response.headers.get('set-cookie') || '';
  return cookie.split(';')[0];
}

async function setupUsers(instance) {
  const setup = await fetch(`${instance.baseUrl}/api/auth/setup`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(ADMIN),
  });
  if (!setup.ok) throw new Error(`setup failed: ${setup.status} ${await setup.text()}`);
  const adminCookie = await apiLogin(instance, ADMIN);
  const created = await fetch(`${instance.baseUrl}/api/auth/users`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Cookie: adminCookie },
    body: JSON.stringify({ ...MEMBER, is_admin: false }),
  });
  if (!created.ok) throw new Error(`member creation failed: ${created.status} ${await created.text()}`);
  return { adminCookie, memberCookie: await apiLogin(instance, MEMBER) };
}

async function pageAs(instance, cookie, viewport = { width: 1280, height: 860 }, colorScheme = 'dark', options = {}) {
  const context = await browser.newContext({ viewport, colorScheme, hasTouch: viewport.width < 600 });
  if (cookie) {
    const [nameValue] = cookie.split(';');
    const [cookieName, ...rest] = nameValue.split('=');
    await context.addCookies([{ name: cookieName, value: rest.join('='), url: instance.baseUrl }]);
  }
  const page = await context.newPage();
  const errors = [];
  const forbidden = [];
  const settingsWrites = [];
  page.on('response', response => {
    if ([401, 403].includes(response.status()) && response.url().includes('/api/')) {
      forbidden.push(`${response.request().method()} ${new URL(response.url()).pathname} ${response.status()}`);
    }
  });
  page.on('request', request => {
    if (request.method() === 'POST' && new URL(request.url()).pathname === '/api/auth/settings') {
      settingsWrites.push(request.url());
    }
  });
  page.on('pageerror', error => errors.push(error.message));
  page.on('console', message => {
    if (message.type() === 'warning' && message.text().includes('Settings registry/DOM mismatch')) {
      errors.push(message.text());
    }
  });
  await page.route('**/api/tasks/notification-logs', route => route.fulfill({ status: 200, contentType: 'application/json', body: '{}' }));
  if (typeof options.beforeGoto === 'function') await options.beforeGoto(page);
  await page.goto(`${instance.baseUrl}/`, { waitUntil: 'domcontentloaded' });
  if (options.waitForStatus !== false) {
    await page.waitForFunction(() => window._isAdmin !== undefined, null, { timeout: 30000 });
  } else {
    await page.waitForFunction(() => window.__odysseusAppStarted === true, null, { timeout: 30000 });
  }
  return { page, context, errors, forbidden, settingsWrites };
}

async function openSettings(page, tab) {
  const vp = page.viewportSize();
  const isNarrow = vp && vp.width < 768;
  if (!isNarrow && await page.isVisible('#user-bar-settings')) {
    await page.click('#user-bar-settings');
  } else {
    // Narrow layouts hide the sidebar button, but its click handler remains
    // available. Exercise the app's existing handler without re-importing.
    await page.locator('#user-bar-settings').evaluate(button => button.click());
  }
  await page.waitForSelector('#settings-modal:not(.hidden)');
  if (tab) {
    await page.click(`[data-settings-tab="${tab}"]`);
    await page.waitForSelector(`[data-settings-panel="${tab}"]:not(.hidden)`);
  }
}

async function searchResults(page, query) {
  await page.fill('#settings-nav-search', query);
  return page.$$eval('[data-settings-search-result]', buttons => buttons.map(button =>
    `${button.dataset.settingsSearchResult}/${button.dataset.settingsSearchSection || '*'}`));
}

async function screenshot(page, name) {
  if (!ARTIFACT_DIR) return;
  mkdirSync(ARTIFACT_DIR, { recursive: true });
  await page.screenshot({ path: join(ARTIFACT_DIR, `${name}.png`) });
}

async function testFinder(instance, cookies) {
  // ── Non-admin member ──
  const member = await pageAs(instance, cookies.memberCookie);
  await openSettings(member.page);
  const landing = await member.page.evaluate(() => ({
    active: document.querySelector('[data-settings-tab].active')?.dataset.settingsTab,
    servicesShown: getComputedStyle(document.querySelector('[data-settings-tab="services"]')).display !== 'none',
    addedShown: getComputedStyle(document.querySelector('[data-settings-tab="added-models"]')).display !== 'none',
  }));
  check('member opens Settings on a panel they can use, without admin endpoint panels', landing.active === 'ai' && !landing.servicesShown && !landing.addedShown, JSON.stringify(landing));
  const memberFallback = await searchResults(member.page, 'fallback');
  check('finder lists cards that own a fallback control', memberFallback.includes('ai/default-chat') && memberFallback.includes('ai/utility-model'), memberFallback.join(','));
  const memberAdminTerms = await searchResults(member.page, 'backup');
  check('finder hides admin-only sections from a member', memberAdminTerms.length === 0, memberAdminTerms.join(','));
  const memberTemperature = await searchResults(member.page, 'temperature');
  check('finder hides the admin-only per-model card from a member', !memberTemperature.includes('added-models/per-model-settings'), memberTemperature.join(','));
  const perModelVisible = await member.page.$eval('#adm-modelCustomCard', node => getComputedStyle(node).display !== 'none');
  check('per-model settings card is not shown to a member', !perModelVisible);
  const voice = await searchResults(member.page, 'voice');
  check('finder omits the hidden text-to-speech card', !voice.includes('ai/text-to-speech') && voice.includes('ai/writing-style'), voice.join(','));
  await screenshot(member.page, 'finder-member-desktop-dark');

  // Keyboard: arrow to the result, Enter reveals the card.
  await member.page.fill('#settings-nav-search', '2fa');
  await member.page.keyboard.press('ArrowDown');
  const activeDescendant = await member.page.getAttribute('#settings-nav-search', 'aria-activedescendant');
  check('finder exposes the active option through aria-activedescendant', activeDescendant === 'settings-search-option-0', String(activeDescendant));
  await member.page.keyboard.press('Enter');
  await member.page.waitForFunction(() => {
    const card = document.querySelector('[data-settings-section="two-factor"]');
    const panel = document.querySelector('[data-settings-panel="account"]');
    return panel && !panel.classList.contains('hidden') && document.activeElement === card;
  }, null, { timeout: 8000 });
  const revealed = await member.page.evaluate(() => {
    const card = document.querySelector('[data-settings-section="two-factor"]');
    const panel = document.querySelector('[data-settings-panel="account"]');
    const rect = card.getBoundingClientRect();
    const content = card.closest('.settings-content') || card.parentElement;
    const box = content.getBoundingClientRect();
    return {
      panelShown: !panel.classList.contains('hidden'),
      activeTab: document.querySelector('[data-settings-tab].active')?.dataset.settingsTab,
      focused: document.activeElement === card,
      inView: rect.top >= box.top - 2 && rect.top < box.bottom,
      expanded: document.querySelector('#settings-nav-search').getAttribute('aria-expanded'),
    };
  });
  check('Enter on a section result opens its panel', revealed.panelShown && revealed.activeTab === 'account', JSON.stringify(revealed));
  check('Enter on a section result focuses and scrolls to the card', revealed.focused && revealed.inView, JSON.stringify(revealed));
  check('finder collapses after activation', revealed.expanded === 'false', JSON.stringify(revealed));

  // Deep section in a long panel: click result for the research model card.
  await member.page.fill('#settings-nav-search', 'research model');
  await member.page.click('[data-settings-search-section="research-model"]');
  await member.page.waitForFunction(() => {
    const card = document.querySelector('[data-settings-section="research-model"]');
    if (!card) return false;
    const rect = card.getBoundingClientRect();
    return document.activeElement === card && rect.top >= 0 && rect.top < window.innerHeight;
  }, null, { timeout: 8000 });
  const research = await member.page.evaluate(() => {
    const card = document.querySelector('[data-settings-section="research-model"]');
    const rect = card.getBoundingClientRect();
    return { focused: document.activeElement === card, top: rect.top, viewport: window.innerHeight };
  });
  check('clicking a result scrolls a lower card into view', research.focused && research.top >= 0 && research.top < research.viewport, JSON.stringify(research));
  await screenshot(member.page, 'finder-member-reveal-research');
  // Programmatic deep link through the public Settings API (same module
  // instance the app uses, located through its own script tag).
  const deepLink = await member.page.evaluate(async () => {
    const src = document.querySelector('script[src*="/static/js/settings.js"]').getAttribute('src');
    const settings = await import(src);
    settings.close();
    settings.open('search', { section: 'deep-research' });
    const start = Date.now();
    while (Date.now() - start < 3000) {
      if (document.querySelector('[data-settings-tab="search"].active') && document.activeElement?.dataset?.settingsSection === 'deep-research') break;
      await new Promise(r => setTimeout(r, 20));
    }
    return {
      activeTab: document.querySelector('[data-settings-tab].active')?.dataset.settingsTab,
      focused: document.activeElement?.dataset?.settingsSection,
    };
  });
  check('open(tab, { section }) lands on the requested card', deepLink.activeTab === 'search' && deepLink.focused === 'deep-research', JSON.stringify(deepLink));
  check('member page has no script errors or registry drift', member.errors.length === 0, member.errors.join(' | '));
  await member.context.close();

  // ── Admin ──
  const admin = await pageAs(instance, cookies.adminCookie);
  await openSettings(admin.page);
  const adminLanding = await admin.page.evaluate(() => document.querySelector('[data-settings-tab].active')?.dataset.settingsTab);
  check('admin still opens Settings on Add Models', adminLanding === 'services', String(adminLanding));
  const adminBackup = await searchResults(admin.page, 'backup');
  check('finder offers admin sections to an admin', adminBackup.includes('system/backup'), adminBackup.join(','));
  await admin.page.click('[data-settings-search-section="backup"]');
  await admin.page.waitForFunction(() => {
    const card = document.querySelector('[data-settings-section="backup"]');
    return document.querySelector('[data-settings-tab="system"].active') && document.activeElement === card;
  }, null, { timeout: 8000 });
  const adminReveal = await admin.page.evaluate(() => ({
    activeTab: document.querySelector('[data-settings-tab].active')?.dataset.settingsTab,
    focused: document.activeElement?.dataset?.settingsSection,
  }));
  check('admin-managed panel result routes through the admin controller and reveals the card', adminReveal.activeTab === 'system' && adminReveal.focused === 'backup', JSON.stringify(adminReveal));
  // A failed server-wide save is reported, not only logged.
  await admin.page.click('[data-settings-tab="reminders"]');
  await admin.page.waitForSelector('[data-settings-panel="reminders"][data-ready="true"]:not(.hidden)');
  await admin.page.route('**/api/auth/settings', route => (
    route.request().method() === 'POST'
      ? route.fulfill({ status: 500, contentType: 'application/json', body: '{}' })
      : route.continue()
  ));
  await admin.page.click('label.admin-switch:has(#set-reminder-llm-toggle)');
  await admin.page.waitForFunction(() => {
    const toast = document.getElementById('toast');
    return toast && toast.classList.contains('show') && toast.textContent.includes('Could not save reminder settings');
  }, null, { timeout: 8000 }).then(() => true, () => false).then(shown => check('failed reminder save shows an error', shown));
  await admin.page.unroute('**/api/auth/settings');

  await admin.page.click('#settings-sidebar-toggle');
  await admin.page.waitForSelector('#settings-nav-search', { state: 'hidden' });
  const collapsedFinder = await admin.page.isVisible('#settings-nav-search');
  await admin.page.click('#settings-sidebar-toggle');
  await admin.page.waitForSelector('#settings-nav-search', { state: 'visible' });
  const expandedFinder = await admin.page.isVisible('#settings-nav-search');
  check('collapsing the desktop sidebar hides the finder with the navigation', !collapsedFinder && expandedFinder, JSON.stringify({ collapsedFinder, expandedFinder }));
  check('admin page has no script errors or registry drift', admin.errors.length === 0, admin.errors.join(' | '));
  await admin.context.close();
}

async function api(instance, cookie, path, init = {}) {
  const response = await fetch(`${instance.baseUrl}${path}`, {
    ...init,
    headers: { 'Content-Type': 'application/json', ...(cookie ? { Cookie: cookie } : {}), ...(init.headers || {}) },
  });
  const text = await response.text();
  let body = null;
  try { body = JSON.parse(text); } catch (_) { body = text; }
  return { status: response.status, body };
}

async function cardState(page, sectionId) {
  return page.evaluate(id => {
    const card = document.querySelector(`[data-settings-section="${id}"]`);
    const badge = card.querySelector('.settings-scope-badge');
    const note = card.querySelector('.settings-scope-note');
    const select = card.querySelector('select');
    return {
      badge: badge ? badge.textContent : null,
      note: note ? note.textContent : null,
      locked: card.classList.contains('settings-scope-locked'),
      firstSelectInert: select ? Boolean(select.closest('[inert]')) : null,
    };
  }, sectionId);
}

async function waitForAiDefaults(page) {
  await page.waitForFunction(() => {
    const select = document.querySelector('#set-defaultEpSelect');
    return select && !select.disabled && Array.from(select.options).some(option => option.value === 'ep-alpha');
  }, null, { timeout: 15000 });
  await page.waitForFunction(() => document.querySelector('[data-settings-section="vision"] .settings-scope-badge'), null, { timeout: 15000 });
}

async function lockedControlsOutsideInert(page) {
  return page.evaluate(() => {
    const leaks = [];
    document.querySelectorAll('.settings-scope-locked').forEach(card => {
      card.querySelectorAll('input, select, textarea, button, label.admin-switch').forEach(control => {
        if (control.closest('.settings-scope-badge')) return;
        if (!control.closest('[inert]')) {
          leaks.push(`${card.dataset.settingsSection}:${control.tagName.toLowerCase()}#${control.id || control.className}`);
        }
      });
    });
    return { lockedCards: document.querySelectorAll('.settings-scope-locked').length, leaks };
  });
}

async function testMemberLoadFailureAndFallbackCompatibility(instance, cookies) {
  const member = await pageAs(instance, cookies.memberCookie);
  // Seed a stored personal default so a failed load has something to hide.
  await api(instance, cookies.memberCookie, '/api/prefs', { method: 'PATCH', body: JSON.stringify({ values: { default_endpoint_id: 'ep-beta', default_model: 'beta-chat' } }) });
  const writes = [];
  member.page.on('request', request => {
    const path = new URL(request.url()).pathname;
    if (request.method() !== 'GET' && (path.startsWith('/api/prefs') || path === '/api/auth/settings')) writes.push(`${request.method()} ${path}`);
  });
  await member.page.route('**/api/prefs', route => (
    route.request().method() === 'GET'
      ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"down"}' })
      : route.continue()
  ));
  await openSettings(member.page, 'ai');
  await member.page.waitForSelector('[data-settings-section="default-chat"] .settings-model-default-retry', { timeout: 15000 });
  const failed = await member.page.evaluate(() => ({
    message: document.querySelector('#set-defaultChatMsg').textContent,
    endpointDisabled: document.querySelector('#set-defaultEpSelect').disabled,
    modelDisabled: document.querySelector('#set-defaultModelSelect').disabled,
    utilityDisabled: document.querySelector('#set-utilityEpSelect').disabled
      && document.querySelector('#set-utilityAddFallback').disabled,
    researchDisabled: document.querySelector('#set-researchEndpoint').disabled,
    fallbackRetryShown: !document.querySelector('#set-foregroundFallbackRetry').hidden,
    fallbackToggleDisabled: document.querySelector('#set-foregroundFallbackToggle').disabled,
  }));
  check('failed model-default load keeps the cards read-only with Retry', failed.message.startsWith('Could not load the saved choice') && failed.endpointDisabled && failed.modelDisabled && failed.utilityDisabled && failed.researchDisabled, JSON.stringify(failed));
  check('PR #5812 fallback editor reports its own load failure', failed.fallbackRetryShown && failed.fallbackToggleDisabled, JSON.stringify(failed));
  await screenshot(member.page, 'member-load-failed');

  await member.page.unroute('**/api/prefs');
  await member.page.click('[data-settings-section="default-chat"] .settings-model-default-retry');
  await member.page.click('#set-foregroundFallbackRetry');
  await member.page.waitForFunction(() => !document.querySelector('#set-defaultEpSelect').disabled && !document.querySelector('#set-foregroundFallbackToggle').disabled, null, { timeout: 10000 });
  const recovered = await member.page.evaluate(() => ({
    endpoint: document.querySelector('#set-defaultEpSelect').value,
    model: document.querySelector('#set-defaultModelSelect').value,
    retryGone: !document.querySelector('[data-settings-section="default-chat"] .settings-model-default-retry'),
    fallbackRetryHidden: document.querySelector('#set-foregroundFallbackRetry').hidden,
  }));
  check('Retry restores the stored personal default and re-enables the card', recovered.endpoint === 'ep-beta' && recovered.model === 'beta-chat' && recovered.retryGone, JSON.stringify(recovered));
  check('PR #5812 fallback Retry recovers with the editor enabled', recovered.fallbackRetryHidden, JSON.stringify(recovered));
  check('no write was issued while values were unknown', writes.length === 0, writes.join(','));

  // PR #5812 runtime behavior: enable fallback, add two candidates, reorder.
  await member.page.click('label.admin-switch:has(#set-foregroundFallbackToggle)');
  await member.page.waitForFunction(() => !document.querySelector('#set-foregroundFallbackEditor').hidden, null, { timeout: 8000 });
  await member.page.click('#set-foregroundAddFallback');
  await member.page.waitForSelector('#set-foregroundFallbacks .settings-fallback-row');
  await member.page.click('#set-foregroundAddFallback');
  await member.page.waitForFunction(() => document.querySelectorAll('#set-foregroundFallbacks .settings-fallback-row').length === 2, null, { timeout: 8000 });
  let beforeMove = [];
  const startAdd = Date.now();
  while (Date.now() - startAdd < 5000) {
    beforeMove = (await api(instance, cookies.memberCookie, '/api/prefs')).body.foreground_model_fallbacks || [];
    if (beforeMove.length === 2) break;
    await new Promise(r => setTimeout(r, 40));
  }
  await member.page.click('#set-foregroundFallbacks .settings-fallback-row:nth-child(2) [data-move-offset="-1"]');
  let prefsAfterMove = {};
  let afterMove = [];
  const startMove = Date.now();
  while (Date.now() - startMove < 5000) {
    prefsAfterMove = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
    afterMove = prefsAfterMove.foreground_model_fallbacks || [];
    if (afterMove.length === 2 && afterMove[0].model === beforeMove[1]?.model) break;
    await new Promise(r => setTimeout(r, 40));
  }
  check('PR #5812 fallback consent and candidates persist per account', prefsAfterMove.foreground_fallback_enabled === true && beforeMove.length === 2, JSON.stringify(prefsAfterMove));
  check('PR #5812 fallback reorder persists', afterMove.length === 2 && afterMove[0].model === beforeMove[1].model && afterMove[1].model === beforeMove[0].model, JSON.stringify({ beforeMove, afterMove }));

  // A failed model-default save leaves the fallback policy alone.
  await member.page.route('**/api/prefs', route => (
    route.request().method() === 'PATCH'
      ? route.fulfill({ status: 500, contentType: 'application/json', body: '{}' })
      : route.continue()
  ));
  await member.page.selectOption('#set-defaultEpSelect', 'ep-alpha');
  await member.page.waitForFunction(() => document.querySelector('#set-defaultChatMsg').textContent.startsWith('Failed to save'), null, { timeout: 8000 });
  await member.page.unroute('**/api/prefs');
  const afterFailure = await member.page.evaluate(() => ({
    toggle: document.querySelector('#set-foregroundFallbackToggle').checked,
    rows: document.querySelectorAll('#set-foregroundFallbacks .settings-fallback-row').length,
    editorShown: !document.querySelector('#set-foregroundFallbackEditor').hidden,
  }));
  const storedAfterFailure = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
  check('failed model-default save leaves the fallback policy intact', afterFailure.toggle && afterFailure.rows === 2 && afterFailure.editorShown && JSON.stringify(storedAfterFailure.foreground_model_fallbacks) === JSON.stringify(afterMove), JSON.stringify({ afterFailure, stored: storedAfterFailure.foreground_model_fallbacks }));
  check('load-failure page has no script errors', member.errors.length === 0, member.errors.join(' | '));
  await member.context.close();
  // Reset the member state the next scenario expects.
  await api(instance, cookies.memberCookie, '/api/prefs', { method: 'PATCH', body: JSON.stringify({ values: { default_endpoint_id: null, default_model: null, foreground_fallback_enabled: null, foreground_model_fallbacks: null } }) });
}

async function testMemberScope(instance, cookies) {
  const member = await pageAs(instance, cookies.memberCookie);
  await openSettings(member.page, 'ai');
  await waitForAiDefaults(member.page);

  const defaultCard = await cardState(member.page, 'default-chat');
  check('member default chat card is labelled as personal', defaultCard.badge === 'Just you' && !defaultCard.locked, JSON.stringify(defaultCard));
  const options = await member.page.$$eval('#set-defaultEpSelect option', nodes => nodes.map(node => node.value));
  check('member chooses from the owner-scoped catalog with an unset option', options[0] === '' && options.includes('ep-alpha') && options.includes('ep-beta'), options.join(','));

  for (const id of ['vision', 'writing-style']) {
    const state = await cardState(member.page, id);
    check(`member sees the server-wide ${id} card locked`, state.badge === 'Set by admin' && state.locked && state.note && state.firstSelectInert !== false, JSON.stringify(state));
  }
  const visionToggleInert = await member.page.$eval('#set-visionEnabledToggle', node => Boolean(node.closest('[inert]')));
  check('locked card heading switch is not operable', visionToggleInert);
  await member.page.waitForFunction(() => document.querySelectorAll('.settings-scope-locked').length >= 2, null, { timeout: 8000 });
  const aiLocks = await lockedControlsOutsideInert(member.page);
  check('every control in locked AI cards is inert after load', aiLocks.lockedCards >= 2 && aiLocks.leaks.length === 0, JSON.stringify(aiLocks));
  for (const tab of ['search', 'reminders']) {
    await member.page.click(`[data-settings-tab="${tab}"]`);
    await member.page.waitForSelector(`[data-settings-panel="${tab}"]:not(.hidden)`);
    await member.page.waitForFunction(t => {
      const p = document.querySelector(`[data-settings-panel="${t}"]`);
      return p && p.querySelectorAll('.settings-scope-locked').length >= 1;
    }, tab, { timeout: 8000 });
    const locks = await lockedControlsOutsideInert(member.page);
    check(`every control in locked ${tab} cards is inert after load`, locks.lockedCards >= 1 && locks.leaks.length === 0, JSON.stringify(locks));
  }
  await member.page.click('[data-settings-tab="ai"]');
  const utilityCard = await cardState(member.page, 'utility-model');
  check('member utility card is personal and editable', utilityCard.badge === 'Just you' && utilityCard.firstSelectInert === false, JSON.stringify(utilityCard));
  await screenshot(member.page, 'scope-member-ai-dark');

  // Personal save, end to end.
  const globalBefore = (await api(instance, cookies.adminCookie, '/api/auth/settings')).body;
  await member.page.selectOption('#set-defaultEpSelect', 'ep-beta');
  await member.page.waitForFunction(() => document.querySelector('#set-defaultChatMsg').textContent === 'Saved', null, { timeout: 8000 });
  const memberPrefs = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
  check('member default is stored in the member preferences', memberPrefs.default_endpoint_id === 'ep-beta' && memberPrefs.default_model === 'beta-chat', JSON.stringify(memberPrefs));
  const globalAfter = (await api(instance, cookies.adminCookie, '/api/auth/settings')).body;
  check('member save leaves the server-wide default untouched', globalAfter.default_endpoint_id === globalBefore.default_endpoint_id && globalAfter.default_model === globalBefore.default_model, JSON.stringify({ before: globalBefore.default_endpoint_id, after: globalAfter.default_endpoint_id }));
  const composerDefault = (await api(instance, cookies.memberCookie, '/api/default-chat')).body;
  check('the chat composer resolves the member default', composerDefault.endpoint_id === 'ep-beta' && composerDefault.model === 'beta-chat', JSON.stringify(composerDefault));

  // Failed save: the form returns to what is stored.
  await member.page.route('**/api/prefs', route => (
    route.request().method() === 'PATCH'
      ? route.fulfill({ status: 500, contentType: 'application/json', body: '{"detail":"boom"}' })
      : route.continue()
  ));
  await member.page.selectOption('#set-defaultEpSelect', 'ep-alpha');
  await member.page.waitForFunction(() => document.querySelector('#set-defaultChatMsg').textContent.startsWith('Failed to save'), null, { timeout: 8000 });
  const restored = await member.page.$eval('#set-defaultEpSelect', node => node.value);
  check('failed personal save restores the stored endpoint', restored === 'ep-beta', restored);
  const stillStored = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
  check('failed personal save wrote nothing', stillStored.default_endpoint_id === 'ep-beta', JSON.stringify(stillStored));
  await member.page.unroute('**/api/prefs');
  await screenshot(member.page, 'scope-member-save-failed');

  // Reopen after reload shows the stored personal value.
  await member.page.reload({ waitUntil: 'domcontentloaded' });
  await member.page.waitForFunction(() => window._isAdmin !== undefined, null, { timeout: 15000 });
  await openSettings(member.page, 'ai');
  await waitForAiDefaults(member.page);
  await member.page.waitForFunction(() => document.querySelector('#set-defaultEpSelect').value === 'ep-beta', null, { timeout: 8000 }).catch(() => {});
  const reopened = await member.page.$eval('#set-defaultEpSelect', node => node.value);
  check('reopened Settings shows the persisted personal default', reopened === 'ep-beta', reopened);

  // Clearing removes the personal choice; any fallback is the server's call.
  await member.page.selectOption('#set-defaultEpSelect', '');
  await member.page.waitForFunction(() => document.querySelector('#set-defaultChatMsg').textContent === 'Saved', null, { timeout: 8000 });
  const cleared = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
  check('choosing "No personal default" removes the personal default', !('default_endpoint_id' in cleared) && !('default_model' in cleared), JSON.stringify(cleared));

  // Utility personal save also stays personal.
  await member.page.selectOption('#set-utilityEpSelect', 'ep-alpha');
  await member.page.waitForFunction(() => document.querySelector('#set-utilityChatMsg').textContent === 'Saved', null, { timeout: 8000 });
  const utilityModelResponse = member.page.waitForResponse(response => {
    const request = response.request();
    if (request.method() !== 'PATCH' ||
        new URL(response.url()).pathname !== '/api/prefs') {
      return false;
    }
    try {
      return JSON.parse(request.postData() || '{}').values?.utility_model === 'alpha-large';
    } catch {
      return false;
    }
  }, { timeout: 10000 });

  await member.page.selectOption('#set-utilityModelSelect', 'alpha-large');
  const utilityModelSaved = await utilityModelResponse;
  if (!utilityModelSaved.ok()) {
    throw new Error(`Utility model save failed: HTTP ${utilityModelSaved.status()}`);
  }
  const utilityPrefs = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
  check('member utility model is stored per account', utilityPrefs.utility_endpoint_id === 'ep-alpha' && utilityPrefs.utility_model === 'alpha-large', JSON.stringify(utilityPrefs));

  // Light theme rendering of the scoped cards using the existing member session.
  await member.page.emulateMedia({ colorScheme: 'light' });
  await member.page.evaluate(() => window.themeModule.applyColors(window.themeModule.THEMES.light));
  await screenshot(member.page, 'scope-member-ai-light');

  // Mobile layout check in a dedicated mobile context exercising actual touch capabilities.
  const mobile = await pageAs(instance, cookies.memberCookie, { width: 390, height: 844 });
  await openSettings(mobile.page, 'ai');
  await waitForAiDefaults(mobile.page);
  const overflowResult = await mobile.page.evaluate(() => {
    const content = document.querySelector('.settings-modal-content');
    if (!content) return { found: false, overflow: -1 };
    return { found: true, overflow: content.scrollWidth - content.clientWidth };
  });
  check(
    'mobile Settings has no horizontal overflow with scope badges',
    overflowResult.found && overflowResult.overflow <= 1,
    overflowResult.found ? String(overflowResult.overflow) : 'missing .settings-modal-content',
  );
  await screenshot(mobile.page, 'scope-member-ai-mobile');
  // The finder stays usable on a phone: visible above the tab rail, with a
  // results list that is not clipped by the horizontally scrolling rail.
  const finderVisible = await mobile.page.isVisible('#settings-nav-search');
  check('mobile Settings shows the finder', finderVisible);
  await mobileFinderAction(mobile.page);
  check('member scope pages have no script errors', member.errors.length === 0 && mobile.errors.length === 0, [...member.errors, ...mobile.errors].join(' | '));
  check('member session never posts server-wide settings', member.settingsWrites.length === 0 && mobile.settingsWrites.length === 0, [...member.settingsWrites, ...mobile.settingsWrites].join(','));
  forbiddenReport.member = [...new Set([...member.forbidden, ...mobile.forbidden])];
  await mobile.context.close();
  await member.context.close();
}

async function mobileFinderAction(page) {
  await page.fill('#settings-nav-search', 'research model');
  const topResult = await page.evaluate(() => {
    const button = document.querySelector('[data-settings-search-section="research-model"]');
    if (!button) return { found: false };
    const rect = button.getBoundingClientRect();
    const hit = document.elementFromPoint(rect.left + rect.width / 2, rect.top + rect.height / 2);
    return { found: true, height: rect.height, hittable: Boolean(hit && button.contains(hit)) };
  });
  check('mobile finder results are on screen and tappable', topResult.found && topResult.height > 20 && topResult.hittable, JSON.stringify(topResult));
  await screenshot(page, 'finder-member-mobile');
  await page.tap('[data-settings-search-section="research-model"]');
  await page.waitForFunction(() => {
    const card = document.querySelector('[data-settings-section="research-model"]');
    if (!card) return false;
    const rect = card.getBoundingClientRect();
    return document.activeElement === card && rect.top >= 0 && rect.top < window.innerHeight;
  }, null, { timeout: 8000 });
  const mobileReveal = await page.evaluate(() => {
    const card = document.querySelector('[data-settings-section="research-model"]');
    const rect = card.getBoundingClientRect();
    return { focused: document.activeElement === card, top: rect.top, viewport: window.innerHeight };
  });
  check('mobile finder result reveals the card', mobileReveal.focused && mobileReveal.top >= 0 && mobileReveal.top < mobileReveal.viewport, JSON.stringify(mobileReveal));
}

// Calls the app's own Settings module, the way other modules deep-link into it.
async function openViaModule(page, tab, section, via = 'settings', minObservationMs = 250) {
  return page.evaluate(async ({ tab, section, via, minObservationMs }) => {
    const src = document.querySelector('script[src*="/static/js/settings.js"]').getAttribute('src');
    const settings = await import(src);
    if (via === 'admin' && window.adminModule) window.adminModule.open(tab);
    else settings.open(tab, section ? { section } : undefined);
    const start = Date.now();
    while (Date.now() - start < 2000) {
      const activeEl = document.querySelector('#settings-modal [data-settings-tab].active');
      const curTab = activeEl ? activeEl.dataset.settingsTab : null;
      const curSection = document.activeElement?.dataset?.settingsSection || null;
      if (curTab === tab && (!section || curSection === section)) break;
      if (curTab && curTab !== tab && Date.now() - start >= minObservationMs) break;
      await new Promise(r => setTimeout(r, 10));
    }
    const active = document.querySelector('#settings-modal [data-settings-tab].active');
    const shown = Array.from(document.querySelectorAll('#settings-modal [data-settings-panel]'))
      .filter(panel => !panel.classList.contains('hidden'))
      .map(panel => panel.dataset.settingsPanel);
    return { active: active ? active.dataset.settingsTab : null, shown, focused: document.activeElement?.dataset?.settingsSection || null };
  }, { tab, section, via, minObservationMs });
}

const RESTRICTED_PANELS = ['services', 'added-models', 'tools', 'users', 'system'];

async function testRestrictedNavigation(instance, cookies) {
  const member = await pageAs(instance, cookies.memberCookie);
  await openSettings(member.page);
  const memberLandings = {};
  for (const tab of RESTRICTED_PANELS) memberLandings[tab] = await openViaModule(member.page, tab);
  memberLandings['added-models#per-model-settings'] = await openViaModule(member.page, 'added-models', 'per-model-settings');
  memberLandings['admin.open(services)'] = await openViaModule(member.page, 'services', null, 'admin');
  const leaked = Object.entries(memberLandings).filter(([key, landing]) => {
    const tab = key.split(/[#(]/)[0].replace('admin.open', 'services');
    return landing.active === tab || landing.shown.some(id => RESTRICTED_PANELS.includes(id)) || landing.shown.length !== 1;
  });
  check('member: explicit open(tab) never activates an admin-only panel', leaked.length === 0, JSON.stringify(memberLandings));
  const adminEndpointsSeen = member.forbidden.filter(entry => entry.includes('/api/diagnostics/logs') || entry.includes('/api/model-endpoints'));
  check('member: adminModule.open(services) triggers no privileged requests', adminEndpointsSeen.length === 0, adminEndpointsSeen.join(','));

  // Negative control: simulate delayed unauthorized panel activation at 120ms (within the 250ms window)
  // to verify that the observation window reliably captures asynchronous/delayed regressions.
  await member.page.evaluate(() => {
    setTimeout(() => {
      const modal = document.getElementById('settings-modal');
      modal?.querySelectorAll('[data-settings-tab]').forEach(t => t.classList.remove('active'));
      modal?.querySelector('[data-settings-tab="services"]')?.classList.add('active');
    }, 120);
  });
  const delayedActivationControl = await openViaModule(member.page, 'services');
  check(
    'negative control: delayed unauthorized panel activation within observation window is detected',
    delayedActivationControl.active === 'services',
    JSON.stringify(delayedActivationControl),
  );
  // Restore member back to a safe permitted tab for remaining checks
  await member.page.evaluate(() => {
    const modal = document.getElementById('settings-modal');
    modal?.querySelectorAll('[data-settings-tab]').forEach(t => t.classList.remove('active'));
    modal?.querySelector('[data-settings-tab="ai"]')?.classList.add('active');
  });

  const memberAccount = await openViaModule(member.page, 'account', 'two-factor');
  check('member: explicit open(tab) still opens panels the member is offered', memberAccount.active === 'account' && memberAccount.focused === 'two-factor', JSON.stringify(memberAccount));
  check('member restricted-navigation page has no script errors', member.errors.length === 0, member.errors.join(' | '));
  await member.context.close();

  const admin = await pageAs(instance, cookies.adminCookie);
  await openSettings(admin.page);
  const adminLandings = {};
  for (const tab of RESTRICTED_PANELS) adminLandings[tab] = await openViaModule(admin.page, tab);
  const adminMissed = RESTRICTED_PANELS.filter(tab => adminLandings[tab].active !== tab || adminLandings[tab].shown.join(',') !== tab);
  check('admin: explicit open(tab) still activates every admin panel', adminMissed.length === 0, JSON.stringify(adminLandings));
  const adminDeep = await openViaModule(admin.page, 'tools', 'agent-limits');
  check('admin: open(tab, { section }) still reveals an admin card', adminDeep.active === 'tools' && adminDeep.focused === 'agent-limits', JSON.stringify(adminDeep));
  check('admin restricted-navigation page has no script errors', admin.errors.length === 0, admin.errors.join(' | '));
  await admin.context.close();
}

async function utilityControls(page) {
  return page.evaluate(() => {
    const rows = Array.from(document.querySelectorAll('#set-utilityFallbacks .settings-fallback-row'));
    const rowControls = Array.from(document.querySelectorAll('#set-utilityFallbacks select, #set-utilityFallbacks button'));
    return {
      rows: rows.length,
      rowModels: rows.map(row => row.querySelectorAll('select')[1]?.value || ''),
      endpoint: document.querySelector('#set-utilityEpSelect').value,
      endpointDisabled: document.querySelector('#set-utilityEpSelect').disabled,
      addDisabled: document.querySelector('#set-utilityAddFallback').disabled,
      rowControls: rowControls.length,
      enabledRowControls: rowControls.filter(control => !control.disabled).map(control => control.getAttribute('aria-label') || control.className),
      message: document.querySelector('#set-utilityChatMsg').textContent,
      retry: Boolean(document.querySelector('[data-settings-section="utility-model"] .settings-model-default-retry')),
    };
  });
}

async function testMemberUtilityFallbackRecovery(instance, cookies) {
  const stored = [{ endpoint_id: 'ep-beta', model: 'beta-chat', reasoning_effort: '' }];
  await api(instance, cookies.memberCookie, '/api/prefs', { method: 'PATCH', body: JSON.stringify({ values: {
    utility_endpoint_id: 'ep-alpha', utility_model: 'alpha-large', utility_model_fallbacks: stored,
  } }) });
  const member = await pageAs(instance, cookies.memberCookie);
  await openSettings(member.page, 'ai');
  await member.page.waitForFunction(() => document.querySelectorAll('#set-utilityFallbacks .settings-fallback-row').length === 1
    && !document.querySelector('#set-utilityEpSelect').disabled, null, { timeout: 15000 });

  // A refused fallback write must not leave the unsaved row on screen.
  await member.page.route('**/api/prefs', route => (
    route.request().method() === 'PATCH'
      ? route.fulfill({ status: 500, contentType: 'application/json', body: '{}' })
      : route.continue()
  ));
  await member.page.click('#set-utilityAddFallback');
  await member.page.waitForFunction(() => document.querySelector('#set-utilityChatMsg').textContent.startsWith('Failed to save'), null, { timeout: 8000 });
  await member.page.waitForFunction(() => document.querySelectorAll('#set-utilityFallbacks .settings-fallback-row').length === 1, null, { timeout: 8000 });
  const afterFailedAdd = await utilityControls(member.page);
  const storedAfterAdd = (await api(instance, cookies.memberCookie, '/api/prefs')).body.utility_model_fallbacks || [];
  check('failed utility fallback save shows the stored list, not the unsaved row', afterFailedAdd.rows === 1 && afterFailedAdd.rowModels[0] === 'beta-chat' && storedAfterAdd.length === 1, JSON.stringify({ afterFailedAdd, storedAfterAdd }));
  await member.page.unroute('**/api/prefs');

  // A failed save whose reload also fails leaves every utility control read-only.
  await member.page.route('**/api/prefs', route => route.fulfill({ status: 500, contentType: 'application/json', body: '{}' }));
  await member.page.selectOption('#set-utilityEpSelect', 'ep-beta');
  await member.page.waitForSelector('[data-settings-section="utility-model"] .settings-model-default-retry', { timeout: 8000 });
  const lockedAfterReloadFailure = await utilityControls(member.page);
  const priorRow = await member.page.$('#set-utilityFallbacks .settings-fallback-row');
  // An endpoint refresh re-renders the fallback rows; they must stay read-only.
  await member.page.click('[data-settings-tab="search"]');
  await member.page.waitForSelector('[data-settings-panel="search"]:not(.hidden)');

  const memberRefreshRequests = [];
  const memberRefreshResponses = [];
  let releaseMemberRefreshGate = null;
  const memberRefreshGate = new Promise(resolve => { releaseMemberRefreshGate = resolve; });
  let memberCatalogRequestsSeen = 0;
  let memberResponseHeld = false;

  await member.page.route('**/api/models*', async route => {
    const url = new URL(route.request().url());
    const reqKey = `${route.request().method()} ${url.pathname}${url.search}`;
    memberRefreshRequests.push(reqKey);
    const isCatalog = url.searchParams.get('foreground_fallback') === 'true';
    if (isCatalog) {
      memberCatalogRequestsSeen += 1;
      // Hold the second sequential catalog response behind the gate
      if (memberCatalogRequestsSeen === 2) {
        memberResponseHeld = true;
        await memberRefreshGate;
      }
    }
    return route.continue();
  });

  const memberResponseListener = response => {
    const url = new URL(response.url());
    if (url.pathname === '/api/models') {
      memberRefreshResponses.push(`${response.request().method()} ${url.pathname}${url.search} ${response.status()}`);
    }
  };
  member.page.on('response', memberResponseListener);

  await member.page.click('[data-settings-tab="ai"]');
  await member.page.waitForSelector('[data-settings-panel="ai"]:not(.hidden)');

  // Wait until the sequential refresh sequence is observed and the second catalog request is held
  const memberDeadline = Date.now() + 8000;
  while (!memberResponseHeld && Date.now() < memberDeadline) {
    await new Promise(r => setTimeout(r, 20));
  }
  if (!memberResponseHeld) {
    throw new Error('Timed out waiting for member refresh sequence to issue second foreground_fallback request');
  }

  // Demonstrate that while the second catalog response is held:
  // 1. The refresh sequence is incomplete (second catalog response has not landed)
  // 2. The previous disabled state cannot pass as completed refresh
  const memberCompletedWhileHeld = memberRefreshResponses.filter(r => r.includes('foreground_fallback=true')).length >= 2;

  // Release the held catalog response to allow the sequential refresh to finish
  releaseMemberRefreshGate();

  // Wait for the complete refresh sequence to finish and the DOM to settle with the re-rendered row
  await member.page.waitForFunction(prev => {
    const current = document.querySelector('#set-utilityFallbacks .settings-fallback-row');
    const epSelect = document.querySelector('#set-utilityEpSelect');
    const retryBtn = document.querySelector('[data-settings-section="utility-model"] .settings-model-default-retry');
    const rowControls = Array.from(document.querySelectorAll('#set-utilityFallbacks select, #set-utilityFallbacks button'));
    return current !== null
      && current !== prev
      && epSelect && epSelect.disabled
      && retryBtn !== null
      && rowControls.length > 0
      && rowControls.every(c => c.disabled);
  }, priorRow, { timeout: 8000 });

  const memberResponseDeadline = Date.now() + 8000;
  while (memberRefreshResponses.filter(r => r.includes('foreground_fallback=true')).length < 2 && Date.now() < memberResponseDeadline) {
    await new Promise(r => setTimeout(r, 20));
  }

  await member.page.unroute('**/api/models*');
  member.page.off('response', memberResponseListener);

  const lockedAfterRefresh = await utilityControls(member.page);
  const fullyLocked = state => state.endpointDisabled && state.addDisabled && state.rowControls > 0 && state.enabledRowControls.length === 0;
  const memberRefreshSequenceVerified = !memberCompletedWhileHeld
    && memberRefreshRequests.filter(r => r.includes('foreground_fallback=true')).length >= 2
    && memberRefreshResponses.filter(r => r.includes('foreground_fallback=true')).length >= 2;
  check('failed utility reload disables the fallback rows as well as the selects', fullyLocked(lockedAfterReloadFailure), JSON.stringify(lockedAfterReloadFailure));
  check('utility fallback rows stay read-only through an endpoint refresh', memberRefreshSequenceVerified && fullyLocked(lockedAfterRefresh), JSON.stringify({ memberRefreshSequenceVerified, requests: memberRefreshRequests, ...lockedAfterRefresh }));
  await member.page.unroute('**/api/prefs');

  const retryResponsePromise = member.page.waitForResponse(response => {
    const url = new URL(response.url());
    return response.request().method() === 'GET' && url.pathname === '/api/prefs' && response.ok();
  }, { timeout: 8000 });
  await member.page.click('[data-settings-section="utility-model"] .settings-model-default-retry');
  await retryResponsePromise;
  await member.page.waitForFunction(() => !document.querySelector('#set-utilityEpSelect').disabled, null, { timeout: 8000 });
  const recovered = await utilityControls(member.page);
  check('utility Retry restores the stored values with the fallback rows editable', recovered.endpoint === 'ep-alpha' && recovered.rows === 1 && recovered.rowModels[0] === 'beta-chat' && recovered.enabledRowControls.length > 0 && !recovered.addDisabled, JSON.stringify(recovered));
  const storedAfter = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
  check('failed utility writes left the stored preferences unchanged', storedAfter.utility_endpoint_id === 'ep-alpha' && JSON.stringify(storedAfter.utility_model_fallbacks) === JSON.stringify(stored), JSON.stringify(storedAfter));
  check('utility recovery page has no script errors', member.errors.length === 0, member.errors.join(' | '));
  await member.context.close();
  await api(instance, cookies.memberCookie, '/api/prefs', { method: 'PATCH', body: JSON.stringify({ values: {
    utility_endpoint_id: null, utility_model: null, utility_model_fallbacks: null,
  } }) });
}

async function testMemberStatusRecovery(instance, cookies) {
  let statusAvailable = false;
  let statusRequests = 0;
  const requests = [];
  const member = await pageAs(instance, cookies.memberCookie, undefined, undefined, {
    waitForStatus: false,
    async beforeGoto(page) {
      await page.route('**/api/auth/status', route => {
        statusRequests += 1;
        return statusAvailable ? route.continue() : route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"unavailable"}' });
      });
      page.on('request', request => {
        const path = new URL(request.url()).pathname;
        if (['/api/prefs', '/api/auth/settings', '/api/model-endpoints'].includes(path)) requests.push(`${request.method()} ${path}`);
      });
    },
  });
  const globalBefore = (await api(instance, cookies.adminCookie, '/api/auth/settings')).body;
  await openSettings(member.page, 'ai');
  await member.page.waitForFunction(() => document.querySelector('[data-settings-section="default-chat"] .settings-model-default-retry')
    || !document.querySelector('#set-defaultEpSelect').disabled, null, { timeout: 15000 });
  await member.page.waitForTimeout(300);
  if (!(await member.page.$('[data-settings-section="default-chat"] .settings-model-default-retry'))) {
    // The card loaded from a store chosen without knowing who the caller is.
    // Record where a member's choice would then be written.
    const probeFrom = requests.length;
    await member.page.selectOption('#set-defaultEpSelect', 'ep-beta').catch(() => {});
    await member.page.waitForTimeout(1500);
    check('unknown access keeps the model-default cards read-only with Retry', false,
      `card editable; requests while unknown: ${requests.slice(0, probeFrom).join(',')}; after a choice: ${requests.slice(probeFrom).join(',') || 'none'}`);
    await member.context.close();
    return;
  }
  const unknown = await member.page.evaluate(() => ({
    defaultDisabled: document.querySelector('#set-defaultEpSelect').disabled,
    utilityDisabled: document.querySelector('#set-utilityEpSelect').disabled,
    researchDisabled: document.querySelector('#set-researchEndpoint').disabled,
  }));
  check('unknown access keeps the model-default cards read-only with Retry', unknown.defaultDisabled && unknown.utilityDisabled && unknown.researchDisabled, JSON.stringify(unknown));
  // Even a change event forced onto the read-only select must not write to
  // any store while it is unknown which one belongs to this caller.
  const forcedFrom = requests.length;
  await member.page.evaluate(() => {
    const select = document.querySelector('#set-defaultEpSelect');
    const option = document.createElement('option');
    option.value = 'ep-forced';
    select.appendChild(option);
    select.value = 'ep-forced';
    select.dispatchEvent(new Event('change', { bubbles: true }));
    option.remove();
  });
  await member.page.waitForTimeout(800);
  const forcedWrites = requests.slice(forcedFrom).filter(entry => !entry.startsWith('GET '));
  const unknownWrites = requests.filter(entry => !entry.startsWith('GET '));
  check('unknown access selects no store: nothing is written, even on a forced change', forcedWrites.length === 0 && unknownWrites.length === 0, unknownWrites.join(','));
  const privilegedWhileFailed = requests.filter(entry => entry.includes('/api/model-endpoints'));
  check('failed status lookup initiates no requests to /api/model-endpoints', privilegedWhileFailed.length === 0, privilegedWhileFailed.join(','));

  statusAvailable = true;
  await member.page.click('[data-settings-section="default-chat"] .settings-model-default-retry');
  await member.page.waitForFunction(() => !document.querySelector('#set-defaultEpSelect').disabled, null, { timeout: 10000 });
  const recovered = await member.page.evaluate(() => ({
    badges: document.querySelectorAll('[data-settings-section="default-chat"] .settings-scope-badge').length,
    badge: document.querySelector('[data-settings-section="default-chat"] .settings-scope-badge')?.textContent || null,
    unsetOption: document.querySelector('#set-defaultEpSelect').options[0]?.value === '',
    offersBeta: Array.from(document.querySelector('#set-defaultEpSelect').options).some(option => option.value === 'ep-beta'),
  }));
  check('Retry after a failed status lookup recovers the personal store', recovered.badges === 1 && recovered.badge === 'Just you' && recovered.unsetOption && recovered.offersBeta, JSON.stringify(recovered));

  const before = requests.length;
  await member.page.selectOption('#set-defaultEpSelect', 'ep-beta');
  await member.page.waitForFunction(() => document.querySelector('#set-defaultChatMsg').textContent === 'Saved', null, { timeout: 8000 });
  await member.page.waitForTimeout(300);
  const saveRequests = requests.slice(before).filter(entry => entry !== 'GET /api/prefs');
  check('member write after recovery is one personal preference write', saveRequests.join(',') === 'PATCH /api/prefs', saveRequests.join(','));

  await member.page.click('[data-settings-section="utility-model"] .settings-model-default-retry');
  await member.page.waitForFunction(() => !document.querySelector('#set-utilityEpSelect').disabled, null, { timeout: 10000 });
  const beforeUtility = requests.length;
  await member.page.selectOption('#set-utilityEpSelect', 'ep-alpha');
  await member.page.waitForFunction(() => document.querySelector('#set-utilityChatMsg').textContent === 'Saved', null, { timeout: 8000 });
  await member.page.waitForTimeout(300);
  const utilityRequests = requests.slice(beforeUtility).filter(entry => entry !== 'GET /api/prefs');
  check('utility card recovers on Retry and writes the member preference once', utilityRequests.join(',') === 'PATCH /api/prefs', utilityRequests.join(','));

  // Closing and reopening Settings must not bind or label anything twice.
  await member.page.click('#settings-modal .close-btn').catch(() => {});
  await openSettings(member.page, 'ai');
  await member.page.waitForSelector('[data-settings-section="default-chat"] .settings-scope-badge');
  const reopenedBadges = await member.page.$$eval('[data-settings-section="default-chat"] .settings-scope-badge', nodes => nodes.length);
  const prefs = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
  const globalAfter = (await api(instance, cookies.adminCookie, '/api/auth/settings')).body;
  check('recovered member choices are stored in the member preferences', prefs.default_endpoint_id === 'ep-beta' && prefs.utility_endpoint_id === 'ep-alpha', JSON.stringify(prefs));
  check('recovered member session never writes server-wide settings', member.settingsWrites.length === 0 && globalAfter.default_endpoint_id === globalBefore.default_endpoint_id && globalAfter.utility_endpoint_id === globalBefore.utility_endpoint_id, JSON.stringify({ writes: member.settingsWrites, before: globalBefore.default_endpoint_id, after: globalAfter.default_endpoint_id }));
  check('status recovery adds no duplicate scope labels', reopenedBadges === 1, String(reopenedBadges));
  check('status was requested again after the failure', statusRequests >= 2, String(statusRequests));
  await screenshot(member.page, 'member-status-recovered');
  check('status recovery page has no script errors', member.errors.length === 0, member.errors.join(' | '));
  await member.context.close();
  await api(instance, cookies.memberCookie, '/api/prefs', { method: 'PATCH', body: JSON.stringify({ values: {
    default_endpoint_id: null, default_model: null, utility_endpoint_id: null, utility_model: null, utility_reasoning_effort: null,
  } }) });
}

async function testPersonalDefaultLabel(instance, cookies) {
  const original = (await api(instance, cookies.adminCookie, '/api/auth/settings')).body.share_defaults_with_users;
  const blank = page => page.$eval('#set-defaultEpSelect', select => {
    const first = select.options[0];
    return {
      value: first?.value,
      text: first?.textContent,
      selected: select.selectedOptions[0]?.value,
      label: select.closest('.settings-row')?.querySelector('label.settings-label')?.textContent,
      texts: Array.from(select.options).map(option => option.textContent),
    };
  });
  try {
    for (const sharing of [false, true]) {
      await api(instance, cookies.adminCookie, '/api/auth/settings', { method: 'POST', body: JSON.stringify({ share_defaults_with_users: sharing }) });
      const mode = sharing ? 'sharing enabled' : 'sharing disabled';
      const member = await pageAs(instance, cookies.memberCookie);
      await openSettings(member.page, 'ai');
      await waitForAiDefaults(member.page);
      const initial = await blank(member.page);
      check(`member (${mode}): the unset choice reads "No personal default"`, initial.value === '' && initial.text === 'No personal default', JSON.stringify(initial));
      check(`member (${mode}): no option promises a server default`, !initial.texts.some(text => /server default/i.test(text)), initial.texts.join(' | '));
      check(`member (${mode}): the endpoint row keeps its label`, initial.label === 'Endpoint', String(initial.label));

      // Wait on the write itself; the "Saved" text of an earlier save can linger.
      const savedChoice = value => Promise.all([
        member.page.waitForResponse(response => response.request().method() === 'PATCH' && new URL(response.url()).pathname === '/api/prefs' && response.ok(), { timeout: 8000 }),
        member.page.selectOption('#set-defaultEpSelect', value),
      ]);
      await savedChoice('ep-beta');
      await savedChoice('');
      const prefs = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
      check(`member (${mode}): clearing removes the personal preference`, !('default_endpoint_id' in prefs) && !('default_model' in prefs), JSON.stringify(prefs));
      const shared = (await api(instance, cookies.adminCookie, '/api/auth/settings')).body;
      check(`member (${mode}): clearing selects no server default and changes no server setting`, shared.share_defaults_with_users === sharing && !('default_endpoint_id' in prefs), JSON.stringify({ sharing: shared.share_defaults_with_users }));

      await member.page.reload({ waitUntil: 'domcontentloaded' });
      await member.page.waitForFunction(() => window._isAdmin !== undefined, null, { timeout: 15000 });
      await openSettings(member.page, 'ai');
      await waitForAiDefaults(member.page);
      const reopened = await blank(member.page);
      check(`member (${mode}): reopened Settings shows "No personal default" selected`, reopened.selected === '' && reopened.text === 'No personal default', JSON.stringify(reopened));
      check(`member (${mode}) label page has no script errors`, member.errors.length === 0, member.errors.join(' | '));
      await member.context.close();
    }
    const admin = await pageAs(instance, cookies.adminCookie);
    await openSettings(admin.page, 'ai');
    await waitForAiDefaults(admin.page);
    const adminOptions = await blank(admin.page);
    check('admin: the server-wide card has no personal-default option', adminOptions.value !== '' && !adminOptions.texts.includes('No personal default'), JSON.stringify(adminOptions));
    await admin.context.close();
  } finally {
    await api(instance, cookies.adminCookie, '/api/auth/settings', { method: 'POST', body: JSON.stringify({ share_defaults_with_users: original }) });
  }
}

async function testAdminLoadFailure(instance, cookies) {
  const admin = await pageAs(instance, cookies.adminCookie);
  await admin.page.route('**/api/auth/settings', route => (
    route.request().method() === 'GET'
      ? route.fulfill({ status: 500, contentType: 'application/json', body: '{}' })
      : route.continue()
  ));
  await openSettings(admin.page, 'ai');
  await admin.page.waitForSelector('[data-settings-section="default-chat"] .settings-model-default-retry', { timeout: 15000 });
  await admin.page.evaluate(async () => {
    const src = document.querySelector('script[src*="/static/js/settings.js"]').getAttribute('src');
    const settings = await import(src);
    await settings.refreshAiModelEndpoints();
  });
  const priorOption = await admin.page.$('#set-defaultEpSelect option');
  // Leave and return: the AI panel refreshes its endpoint lists on entry.
  await admin.page.click('[data-settings-tab="search"]');
  await admin.page.waitForSelector('[data-settings-panel="search"]:not(.hidden)');

  const adminRefreshRequests = [];
  const adminRefreshResponses = [];
  let releaseAdminRefreshGate = null;
  const adminRefreshGate = new Promise(resolve => { releaseAdminRefreshGate = resolve; });
  let adminResponseHeld = false;

  await admin.page.route('**/api/model-endpoints', async route => {
    adminRefreshRequests.push(`${route.request().method()} /api/model-endpoints`);
    return route.continue();
  });

  await admin.page.route('**/api/models*', async route => {
    const url = new URL(route.request().url());
    const reqKey = `${route.request().method()} ${url.pathname}${url.search}`;
    adminRefreshRequests.push(reqKey);
    if (url.searchParams.get('foreground_fallback') === 'true') {
      adminResponseHeld = true;
      await adminRefreshGate;
    }
    return route.continue();
  });

  const adminResponseListener = response => {
    const url = new URL(response.url());
    if (url.pathname === '/api/model-endpoints' || url.pathname === '/api/models') {
      adminRefreshResponses.push(`${response.request().method()} ${url.pathname}${url.search} ${response.status()}`);
    }
  };
  admin.page.on('response', adminResponseListener);

  await admin.page.click('[data-settings-tab="ai"]');
  await admin.page.waitForSelector('[data-settings-panel="ai"]:not(.hidden)');

  // Wait until the sequential refresh sequence reaches the foreground fallback request and holds it
  const adminDeadline = Date.now() + 8000;
  while (!adminResponseHeld && Date.now() < adminDeadline) {
    await new Promise(r => setTimeout(r, 20));
  }
  if (!adminResponseHeld) {
    throw new Error('Timed out waiting for admin refresh sequence to issue foreground_fallback request');
  }

  // Demonstrate that while the foreground fallback response is held:
  // 1. The refresh sequence is incomplete
  const adminCompletedWhileHeld = adminRefreshResponses.some(r => r.includes('foreground_fallback=true'));

  // Release the held catalog response to allow the sequential refresh to finish
  releaseAdminRefreshGate();

  // Wait for the complete refresh sequence to finish and DOM options to re-populate
  await admin.page.waitForFunction(prev => {
    const epSelect = document.querySelector('#set-defaultEpSelect');
    const modelSelect = document.querySelector('#set-defaultModelSelect');
    const retryBtn = document.querySelector('[data-settings-section="default-chat"] .settings-model-default-retry');
    const currentOption = epSelect ? epSelect.querySelector('option') : null;
    return epSelect && epSelect.disabled
      && modelSelect && modelSelect.disabled
      && retryBtn !== null
      && currentOption !== null
      && currentOption !== prev;
  }, priorOption, { timeout: 8000 });

  const adminResponseDeadline = Date.now() + 8000;
  while (!adminRefreshResponses.some(r => r.includes('foreground_fallback=true')) && Date.now() < adminResponseDeadline) {
    await new Promise(r => setTimeout(r, 20));
  }

  await admin.page.unroute('**/api/models*');
  await admin.page.unroute('**/api/model-endpoints');
  admin.page.off('response', adminResponseListener);

  const state = await admin.page.evaluate(() => ({
    endpointDisabled: document.querySelector('#set-defaultEpSelect').disabled,
    modelDisabled: document.querySelector('#set-defaultModelSelect').disabled,
    retryShown: Boolean(document.querySelector('[data-settings-section="default-chat"] .settings-model-default-retry')),
  }));

  const adminRefreshSequenceVerified = !adminCompletedWhileHeld
    && adminRefreshRequests.some(r => r.includes('/api/model-endpoints'))
    && adminRefreshRequests.some(r => r.includes('foreground_fallback=true'))
    && adminRefreshResponses.some(r => r.includes('/api/model-endpoints'))
    && adminRefreshResponses.some(r => r.includes('foreground_fallback=true'));

  check('admin card stays read-only through an endpoint refresh while its values are unknown', adminRefreshSequenceVerified && state.endpointDisabled && state.modelDisabled && state.retryShown, JSON.stringify({ adminRefreshSequenceVerified, requests: adminRefreshRequests, ...state }));
  await admin.page.unroute('**/api/auth/settings');

  const retryResponsePromise = admin.page.waitForResponse(response => {
    const url = new URL(response.url());
    return response.request().method() === 'GET' && url.pathname === '/api/auth/settings' && response.ok();
  }, { timeout: 8000 });
  await admin.page.click('[data-settings-section="default-chat"] .settings-model-default-retry');
  await retryResponsePromise;
  await admin.page.waitForFunction(() => !document.querySelector('#set-defaultEpSelect').disabled
    && !document.querySelector('#set-defaultModelSelect').disabled, null, { timeout: 8000 }).catch(() => {});
  const recovered = await admin.page.evaluate(() => ({
    endpointDisabled: document.querySelector('#set-defaultEpSelect').disabled,
    modelDisabled: document.querySelector('#set-defaultModelSelect').disabled,
  }));
  check('admin Retry re-enables the card', !recovered.endpointDisabled && !recovered.modelDisabled, JSON.stringify(recovered));
  await admin.context.close();
}

async function testAdminScope(instance, cookies) {
  // A stale personal value (as a Cookbook serve can leave) must not keep
  // shadowing the server default the admin just chose.
  await api(instance, cookies.adminCookie, '/api/prefs', { method: 'PATCH', body: JSON.stringify({ values: { default_endpoint_id: 'ep-beta', default_model: 'beta-chat' } }) });
  const admin = await pageAs(instance, cookies.adminCookie);
  await openSettings(admin.page, 'ai');
  await waitForAiDefaults(admin.page);
  const defaultCard = await cardState(admin.page, 'default-chat');
  check('admin default chat card is labelled as the server default', defaultCard.badge === 'Server default' && !defaultCard.locked, JSON.stringify(defaultCard));
  const vision = await cardState(admin.page, 'vision');
  check('admin server-wide cards are editable and labelled for all users', vision.badge === 'All users' && !vision.locked && vision.firstSelectInert === false, JSON.stringify(vision));
  const blankOption = await admin.page.$$eval('#set-defaultEpSelect option', nodes => nodes.some(node => node.value === ''));
  check('admin server default keeps its existing required endpoint choice', !blankOption);

  const preSelect = await admin.page.evaluate(() => ({
    value: document.querySelector('#set-defaultEpSelect').value,
    disabled: document.querySelector('#set-defaultEpSelect').disabled,
    message: document.querySelector('#set-defaultChatMsg').textContent,
  }));
  await admin.page.selectOption('#set-defaultEpSelect', 'ep-alpha');
  const savedShown = await admin.page.waitForFunction(() => document.querySelector('#set-defaultChatMsg').textContent === 'Saved', null, { timeout: 8000 }).then(() => true, () => false);
  if (!savedShown) {
    const postSelect = await admin.page.evaluate(() => ({
      value: document.querySelector('#set-defaultEpSelect').value,
      disabled: document.querySelector('#set-defaultEpSelect').disabled,
      message: document.querySelector('#set-defaultChatMsg').textContent,
    }));
    check('admin default save reports Saved', false, JSON.stringify({ preSelect, postSelect, writes: admin.settingsWrites.length }));
  }
  const globalSettings = (await api(instance, cookies.adminCookie, '/api/auth/settings')).body;
  check('admin save writes the server-wide default', globalSettings.default_endpoint_id === 'ep-alpha' && globalSettings.default_model === 'alpha-large', JSON.stringify({ ep: globalSettings.default_endpoint_id, model: globalSettings.default_model }));
  const adminPrefs = (await api(instance, cookies.adminCookie, '/api/prefs')).body;
  check('admin save clears the admin\'s own shadowing preference', !('default_endpoint_id' in adminPrefs) && !('default_model' in adminPrefs), JSON.stringify(adminPrefs));
  const memberPrefs = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
  check('admin save does not touch member preferences', memberPrefs.utility_endpoint_id === 'ep-alpha', JSON.stringify(memberPrefs));
  await screenshot(admin.page, 'scope-admin-ai-dark');
  check('admin scope page has no script errors', admin.errors.length === 0, admin.errors.join(' | '));
  forbiddenReport.admin = [...new Set(admin.forbidden)];
  await admin.context.close();
}

async function testAuthDisabledOperator(instance) {
  const operator = await pageAs(instance, null);
  await openSettings(operator.page);
  await operator.page.waitForFunction(() => getComputedStyle(document.querySelector('[data-settings-tab="tools"]')).display !== 'none', null, { timeout: 10000 }).catch(() => {});
  const nav = await operator.page.evaluate(() => Object.fromEntries(['tools', 'system', 'users'].map(id => [
    id, getComputedStyle(document.querySelector(`[data-settings-tab="${id}"]`)).display !== 'none',
  ])));
  check('auth-disabled operator sees the admin panels the server lets them use', nav.tools && nav.system, JSON.stringify(nav));
  check('auth-disabled operator is not offered user management', !nav.users, JSON.stringify(nav));
  const usersSearch = await searchResults(operator.page, 'signup');
  check('auth-disabled finder omits user management', usersSearch.length === 0, usersSearch.join(','));
  await operator.page.fill('#settings-nav-search', '');
  const operatorTools = await openViaModule(operator.page, 'tools', 'agent-limits');
  const operatorUsers = await openViaModule(operator.page, 'users');
  check('auth-disabled operator: explicit open(tab) reaches admin panels', operatorTools.active === 'tools' && operatorTools.focused === 'agent-limits', JSON.stringify(operatorTools));
  check('auth-disabled operator: explicit open(tab) does not activate user management', operatorUsers.active !== 'users' && !operatorUsers.shown.includes('users') && operatorUsers.shown.length === 1, JSON.stringify(operatorUsers));
  await operator.page.fill('#settings-nav-search', '');
  await operator.page.click('[data-settings-tab="ai"]');
  await operator.page.waitForFunction(() => Array.from(document.querySelector('#set-defaultEpSelect').options).some(o => o.value === 'ep-alpha'), null, { timeout: 15000 });
  const badges = await operator.page.$$eval('.settings-scope-badge', nodes => nodes.length);
  check('single-user install shows no per-user scope labels', badges === 0, String(badges));
  await operator.page.selectOption('#set-defaultEpSelect', 'ep-beta');
  await operator.page.waitForFunction(() => document.querySelector('#set-defaultChatMsg').textContent === 'Saved', null, { timeout: 8000 });
  const settings = (await api(instance, null, '/api/auth/settings')).body;
  check('auth-disabled operator saves the server-wide default', settings.default_endpoint_id === 'ep-beta', JSON.stringify({ ep: settings.default_endpoint_id }));
  await operator.page.click('[data-settings-tab="added-models"]');
  await operator.page.waitForSelector('[data-settings-panel="added-models"]:not(.hidden)');
  const perModel = await operator.page.$eval('#adm-modelCustomCard', node => getComputedStyle(node).display !== 'none');
  check('auth-disabled operator can reach per-model settings', perModel);
  await screenshot(operator.page, 'operator-added-models');
  await operator.page.click('[data-settings-tab="tools"]');
  await operator.page.waitForFunction(() => document.querySelectorAll('#adm-builtin-tools-list > *').length > 0, null, { timeout: 15000 }).catch(() => {});
  const tools = await operator.page.evaluate(() => ({
    builtinTools: document.querySelectorAll('#adm-builtin-tools-list > *').length,
    approval: document.querySelector('#set-toolApprovalMode').value,
  }));
  check('auth-disabled operator Agent Tools panel loads its content', tools.builtinTools > 0 && Boolean(tools.approval), JSON.stringify(tools));
  // The newly exposed panels must also accept the operator's writes.
  await operator.page.fill('#set-agentMaxRounds', '37');
  await operator.page.press('#set-agentMaxRounds', 'Tab');
  await operator.page.waitForFunction(() => document.querySelector('#set-agentMsg').textContent.includes('37 steps'), null, { timeout: 8000 }).catch(() => {});
  const agentSettings = (await api(instance, null, '/api/auth/settings')).body;
  check('auth-disabled operator can save Agent Tools limits', agentSettings.agent_max_rounds === 37, JSON.stringify({ rounds: agentSettings.agent_max_rounds, msg: await operator.page.textContent('#set-agentMsg') }));
  const exportStatus = await operator.page.evaluate(async () => (await fetch('/api/export', { credentials: 'same-origin' })).status);
  check('auth-disabled operator can use System backup export', exportStatus === 200, String(exportStatus));
  await operator.page.click('[data-settings-tab="system"]');
  await operator.page.waitForSelector('[data-settings-panel="system"]:not(.hidden)');
  const system = await operator.page.evaluate(() => ({
    backupShown: getComputedStyle(document.querySelector('[data-settings-section="backup"]')).display !== 'none',
    dangerShown: getComputedStyle(document.querySelector('[data-settings-section="danger-zone"]')).display !== 'none',
  }));
  check('auth-disabled operator System panel shows backup and danger zone', system.backupShown && system.dangerShown, JSON.stringify(system));
  forbiddenReport.operator = [...new Set(operator.forbidden)];
  check('operator page has no script errors', operator.errors.length === 0, operator.errors.join(' | '));
  await operator.context.close();
}

async function testDelayedAuthorizationResolution(instance, cookies) {
  // Member with delayed status
  let memberStatusReleased = false;
  const memberPrivilegedRequests = [];
  const member = await pageAs(instance, cookies.memberCookie, undefined, undefined, {
    waitForStatus: false,
    async beforeGoto(page) {
      await page.route('**/api/auth/status', async route => {
        while (!memberStatusReleased) {
          await new Promise(r => setTimeout(r, 100));
        }
        return route.continue();
      });
      page.on('request', request => {
        const path = new URL(request.url()).pathname;
        if ([
          '/api/model-endpoints',
          '/api/contacts/config',
          '/api/contacts/list',
          '/api/auth/integrations',
          '/api/mcp/servers',
          '/api/vault/config',
          '/api/tokens',
          '/api/diagnostics/logs',
        ].includes(path)) {
          memberPrivilegedRequests.push(`${request.method()} ${path}`);
        }
      });
    },
  });

  await openSettings(member.page);
  await member.page.waitForTimeout(500);

  check(
    'member: privileged initialization does not begin while access is unknown',
    memberPrivilegedRequests.length === 0,
    memberPrivilegedRequests.join(',')
  );

  const memberStatusPromise = member.page.waitForResponse(
    r => r.url().includes('/api/auth/status') && r.status() === 200,
    { timeout: 15000 }
  );
  memberStatusReleased = true;
  await memberStatusPromise;
  await member.page.waitForFunction(() => {
    const tab = document.querySelector('[data-settings-tab].active');
    const services = document.querySelector('[data-settings-tab="services"]');
    return window._isAdmin === false &&
      window._canManageSettings === false &&
      tab && tab.dataset.settingsTab === 'ai' &&
      services && getComputedStyle(services).display === 'none';
  }, null, { timeout: 15000 });

  const memberLanding = await member.page.evaluate(() => ({
    active: document.querySelector('[data-settings-tab].active')?.dataset.settingsTab,
    servicesShown: getComputedStyle(document.querySelector('[data-settings-tab="services"]')).display !== 'none',
  }));
  check(
    'member: Settings resolves to allowed panel after delayed status',
    memberLanding.active === 'ai' && !memberLanding.servicesShown,
    JSON.stringify(memberLanding)
  );
  check(
    'member: zero privileged requests made after delayed status resolution',
    memberPrivilegedRequests.length === 0,
    memberPrivilegedRequests.join(',')
  );
  check('member delayed status page has no script errors', member.errors.length === 0, member.errors.join(' | '));
  await member.context.close();

  // Admin with delayed status
  let adminStatusReleased = false;
  const adminPrivilegedRequests = [];
  const admin = await pageAs(instance, cookies.adminCookie, undefined, undefined, {
    waitForStatus: false,
    async beforeGoto(page) {
      await page.route('**/api/auth/status', async route => {
        while (!adminStatusReleased) {
          await new Promise(r => setTimeout(r, 100));
        }
        return route.continue();
      });
      page.on('request', request => {
        const path = new URL(request.url()).pathname;
        if ([
          '/api/model-endpoints',
          '/api/contacts/config',
          '/api/contacts/list',
          '/api/auth/integrations',
          '/api/mcp/servers',
          '/api/vault/config',
          '/api/tokens',
          '/api/diagnostics/logs',
        ].includes(path)) {
          adminPrivilegedRequests.push(`${request.method()} ${path}`);
        }
      });
    },
  });

  await openSettings(admin.page);
  await admin.page.waitForTimeout(500);

  check(
    'admin: privileged initialization does not begin while access is unknown',
    adminPrivilegedRequests.length === 0,
    adminPrivilegedRequests.join(',')
  );

  const adminStatusPromise = admin.page.waitForResponse(
    r => r.url().includes('/api/auth/status') && r.status() === 200,
    { timeout: 15000 }
  );
  adminStatusReleased = true;
  await adminStatusPromise;
  await admin.page.waitForFunction(() => {
    const services = document.querySelector('[data-settings-tab="services"]');
    return window._isAdmin === true &&
      window._canManageSettings === true &&
      services && getComputedStyle(services).display !== 'none';
  }, null, { timeout: 15000 });

  const adminVisibility = await admin.page.evaluate(() => ({
    servicesShown: getComputedStyle(document.querySelector('[data-settings-tab="services"]')).display !== 'none',
  }));
  check(
    'admin: admin panels become available after status resolves',
    adminVisibility.servicesShown,
    JSON.stringify(adminVisibility)
  );

  await admin.page.click('[data-settings-tab="services"]');
  await admin.page.waitForSelector('[data-settings-tab="services"].active');

  const adminActive = await admin.page.evaluate(() => document.querySelector('[data-settings-tab].active')?.dataset.settingsTab);
  check(
    'admin: navigating to admin panel activates services',
    adminActive === 'services',
    String(adminActive)
  );

  await admin.page.waitForFunction(() => {
    const sel = document.querySelector('#set-defaultEpSelect');
    return sel && sel.options.length > 0;
  }, null, { timeout: 15000 }).catch(() => {});
  check(
    'admin: privileged requests occurred after status resolved',
    adminPrivilegedRequests.length > 0,
    adminPrivilegedRequests.join(',')
  );
  check('admin delayed status page has no script errors', admin.errors.length === 0, admin.errors.join(' | '));
  await admin.context.close();
}

// Admin-managed lists the Integrations panel reads. A caller who may not manage
// settings must never cause these to be requested.
const INTEGRATION_ADMIN_PATHS = [
  '/api/auth/integrations',
  '/api/contacts/config',
  '/api/contacts/list',
  '/api/mcp/servers',
  '/api/vault/config',
  '/api/tokens',
];

// Opens Settings while /api/auth/status is failing, then lets it recover.
// `status.mode` is 'fail' (HTTP 503), 'hold' (answer only once `status.release`
// is called) or 'ok'.
async function integrationsRecoveryPage(instance, cookie) {
  const status = { mode: 'fail', requests: 0, release: () => {}, gate: null };
  const adminRequests = [];
  let navigations = 0;
  const session = await pageAs(instance, cookie, undefined, undefined, {
    waitForStatus: false,
    async beforeGoto(page) {
      page.on('load', () => { navigations += 1; });
      page.on('request', request => {
        const path = new URL(request.url()).pathname;
        if (request.method() === 'GET' && INTEGRATION_ADMIN_PATHS.includes(path)) adminRequests.push(path);
      });
      await page.route('**/api/auth/status', async route => {
        status.requests += 1;
        if (status.mode === 'hold') await status.gate;
        if (status.mode === 'fail') {
          return route.fulfill({ status: 503, contentType: 'application/json', body: '{"detail":"unavailable"}' });
        }
        return route.continue();
      });
    },
  });
  status.hold = () => {
    status.mode = 'hold';
    status.gate = new Promise(resolveGate => { status.release = () => { status.mode = 'ok'; resolveGate(); }; });
  };
  return { ...session, status, adminRequests, navigations: () => navigations };
}

async function closeAndReopenSettings(page, tab) {
  await page.click('#settings-modal .close-btn');
  await page.waitForSelector('#settings-modal.hidden', { state: 'attached' });
  await openSettings(page, tab);
}

const INTEGRATION_CARD = '#unified-integrations-list .intg-card[data-intg-type="api"]';

async function testIntegrationsAuthRecovery(instance, cookies, { admin, authDisabled = false }) {
  const label = authDisabled ? 'operator' : admin ? 'admin' : 'member';
  const cookie = authDisabled ? null : admin ? cookies.adminCookie : cookies.memberCookie;
  const probeName = 'Recovery Probe';
  let seededId = null;
  if (admin) {
    const created = await api(instance, cookie, '/api/auth/integrations', {
      method: 'POST',
      body: JSON.stringify({ name: probeName, base_url: 'http://127.0.0.1:9/v1', api_key: 'recovery-probe-key' }),
    });
    seededId = created.body?.integration?.id;
    check(`${label} recovery: the saved integration was seeded`, Boolean(seededId), JSON.stringify(created));
  }

  const session = await integrationsRecoveryPage(instance, cookie);
  const { page } = session;
  await page.evaluate(() => { window.__noReloadMarker = 'kept'; });
  await openSettings(page, 'integrations');
  await page.waitForFunction(() => /No integrations configured/.test(document.querySelector('#unified-integrations-list')?.textContent || ''), null, { timeout: 15000 });
  check(`${label} recovery: unknown access requests no admin-managed lists`, session.adminRequests.length === 0, session.adminRequests.join(','));

  // Work in progress that recovery must not discard: an open create form.
  await page.click('#unified-intg-add-btn');
  await page.click('.uf-type-option[data-value="api"]');
  await page.fill('#uf-api-name', 'Draft in progress');
  const adminRequestsBeforeRecovery = session.adminRequests.length;

  session.status.mode = 'ok';
  const listResponsePromise = admin ? page.waitForResponse(
    r => r.url().includes('/api/auth/integrations') && r.request().method() === 'GET',
    { timeout: 10000 },
  ) : null;
  await closeAndReopenSettings(page, 'integrations');

  if (admin) {
    await listResponsePromise;
    await page.waitForSelector(INTEGRATION_CARD, { timeout: 10000 }).catch(() => {});
    const shown = await page.$$eval(INTEGRATION_CARD, nodes => nodes.map(node => node.textContent.replace(/\s+/g, ' ').trim()));
    check(`${label} recovery: the existing integration appears without a reload`, shown.some(text => text.includes(probeName)), JSON.stringify(shown));
    check(`${label} recovery: no placeholder remains once the list is loaded`, !(await page.textContent('#unified-integrations-list')).includes('No integrations configured'));
    const listRequests = session.adminRequests.slice(adminRequestsBeforeRecovery).filter(path => path === '/api/auth/integrations');
    check(`${label} recovery: the list is requested exactly once`, listRequests.length === 1, String(listRequests.length));
    const form = await page.evaluate(() => ({
      open: getComputedStyle(document.querySelector('#unified-intg-form')).display !== 'none',
      draft: document.querySelector('#uf-api-name')?.value,
    }));
    check(`${label} recovery: an open form and its draft survive the refresh`, form.open && form.draft === 'Draft in progress', JSON.stringify(form));
    // A second, later open must not fetch again or duplicate cards.
    await closeAndReopenSettings(page, 'integrations');
    await page.waitForTimeout(500);
    const cards = await page.$$eval(INTEGRATION_CARD, nodes => nodes.length);
    const total = session.adminRequests.filter(path => path === '/api/auth/integrations').length;
    check(`${label} recovery: reopening adds no duplicate cards or list requests`, cards === 1 && total === 1, JSON.stringify({ cards, total }));
    await screenshot(page, 'integrations-admin-recovered');
  } else {
    // Preserve full 1200ms observation window to maintain sensitivity to delayed unauthorized requests
    await page.waitForTimeout(1200);
    check('member recovery: no admin-managed list is ever requested', session.adminRequests.length === 0, session.adminRequests.join(','));
    const view = await page.evaluate(() => ({
      apiCards: document.querySelectorAll('#unified-integrations-list .intg-card[data-intg-type="api"]').length,
      servicesShown: getComputedStyle(document.querySelector('[data-settings-tab="services"]')).display !== 'none',
    }));
    check('member recovery: restricted controls stay unavailable', view.apiCards === 0 && !view.servicesShown, JSON.stringify(view));
  }
  check(`${label} recovery: status was asked again after the failure`, session.status.requests >= 2, String(session.status.requests));
  check(`${label} recovery: the page was never reloaded`, session.navigations() === 1 && await page.evaluate(() => window.__noReloadMarker) === 'kept', String(session.navigations()));
  check(`${label} recovery page has no script errors`, session.errors.length === 0, session.errors.join(' | '));
  await session.context.close();
  if (seededId) await api(instance, cookie, `/api/auth/integrations/${seededId}`, { method: 'DELETE' });
}

async function testIntegrationsRapidReopenDuringRecovery(instance, cookies) {
  const created = await api(instance, cookies.adminCookie, '/api/auth/integrations', {
    method: 'POST',
    body: JSON.stringify({ name: 'Rapid Probe', base_url: 'http://127.0.0.1:9/v1', api_key: 'rapid-probe-key' }),
  });
  const seededId = created.body?.integration?.id;
  const session = await integrationsRecoveryPage(instance, cookies.adminCookie);
  const { page } = session;
  await openSettings(page, 'integrations');
  await page.waitForFunction(() => /No integrations configured/.test(document.querySelector('#unified-integrations-list')?.textContent || ''), null, { timeout: 15000 });
  const requestsBefore = session.status.requests;

  // The retry is in flight while Settings is closed and reopened repeatedly.
  session.status.hold();
  await closeAndReopenSettings(page, 'integrations');
  await closeAndReopenSettings(page, 'integrations');
  await closeAndReopenSettings(page, 'integrations');
  await page.waitForTimeout(300);
  check('admin rapid reopen: overlapping retries share one status request', session.status.requests - requestsBefore === 1, String(session.status.requests - requestsBefore));
  check('admin rapid reopen: nothing privileged is requested while the answer is pending', session.adminRequests.length === 0, session.adminRequests.join(','));
  const listResponsePromise = page.waitForResponse(
    r => r.url().includes('/api/auth/integrations') && r.request().method() === 'GET',
    { timeout: 10000 },
  );
  session.status.release();
  await listResponsePromise;
  await page.waitForSelector(INTEGRATION_CARD, { timeout: 10000 }).catch(() => {});
  await page.waitForTimeout(300);
  const cards = await page.$$eval(INTEGRATION_CARD, nodes => nodes.length);
  const listRequests = session.adminRequests.filter(path => path === '/api/auth/integrations').length;
  check('admin rapid reopen: the integration appears once with one list request', cards === 1 && listRequests === 1, JSON.stringify({ cards, listRequests }));
  check('admin rapid reopen page has no script errors', session.errors.length === 0, session.errors.join(' | '));
  await session.context.close();
  if (seededId) await api(instance, cookies.adminCookie, `/api/auth/integrations/${seededId}`, { method: 'DELETE' });
}

async function testIntegrationsIgnoreStaleListResponse(instance, cookies) {
  const ids = [];
  for (const name of ['Stale Probe A', 'Stale Probe B']) {
    const created = await api(instance, cookies.adminCookie, '/api/auth/integrations', {
      method: 'POST',
      body: JSON.stringify({ name, base_url: 'http://127.0.0.1:9/v1', api_key: `${name}-key` }),
    });
    ids.push(created.body?.integration?.id);
  }
  const session = await pageAs(instance, cookies.adminCookie);
  const { page } = session;
  await openSettings(page, 'integrations');
  await page.waitForFunction(() => document.querySelectorAll('#unified-integrations-list .intg-card[data-intg-type="api"]').length === 2, null, { timeout: 15000 });
  await page.evaluate(() => { window.styledConfirm = async () => true; });

  // The first list refresh answers late with the pre-delete snapshot.
  const staleBody = JSON.stringify((await api(instance, cookies.adminCookie, '/api/auth/integrations')).body);
  // Only requests made after the first delete are counted, so the held one is
  // the refresh that delete triggers and not an unrelated earlier reader.
  const listCalls = { count: 0, armed: false };
  let releaseStale = () => {};
  const staleGate = new Promise(resolveGate => { releaseStale = resolveGate; });
  await page.route('**/api/auth/integrations', async route => {
    if (route.request().method() !== 'GET' || !listCalls.armed) return route.continue();
    listCalls.count += 1;
    if (listCalls.count === 1) {
      await staleGate;
      return route.fulfill({ status: 200, contentType: 'application/json', body: staleBody });
    }
    return route.continue();
  });

  listCalls.armed = true;
  await page.click(`#unified-integrations-list .intg-del-btn[data-intg-id="${ids[0]}"]`);
  while (listCalls.count < 1) await new Promise(resolveWait => setTimeout(resolveWait, 25));
  await page.click(`#unified-integrations-list .intg-del-btn[data-intg-id="${ids[1]}"]`);
  await page.waitForFunction(() => /No integrations configured/.test(document.querySelector('#unified-integrations-list')?.textContent || ''), null, { timeout: 10000 });

  releaseStale();
  await page.waitForTimeout(600);
  const cards = await page.$$eval('#unified-integrations-list .intg-card[data-intg-type="api"]', nodes => nodes.length);
  check('a late, stale list response does not replace the newer list', cards === 0 && listCalls.count >= 2, JSON.stringify({ cards, calls: listCalls.count }));
  check('stale list response page has no script errors', session.errors.length === 0, session.errors.join(' | '));
  await session.context.close();
  for (const id of ids) if (id) await api(instance, cookies.adminCookie, `/api/auth/integrations/${id}`, { method: 'DELETE' });
}

async function testEarlyEndpointRefreshAndRepeatedOpen(instance, cookies) {
  const session = await pageAs(instance, cookies.adminCookie);

  // 1. Invoke refreshAiModelEndpoints() BEFORE Settings is opened or initialized.
  const earlyRefreshResult = await session.page.evaluate(async () => {
    const src = document.querySelector('script[src*="/static/js/settings.js"]').getAttribute('src');
    const settings = await import(src);
    try {
      await settings.refreshAiModelEndpoints();
      return { success: true };
    } catch (e) {
      return { success: false, error: String(e) };
    }
  });
  check('early refreshAiModelEndpoints() before Settings opens succeeds without error', earlyRefreshResult.success, JSON.stringify(earlyRefreshResult));

  // 2. Open Settings for the first time.
  await openSettings(session.page, 'ai');
  await waitForAiDefaults(session.page);
  const initialOptionsCount = await session.page.$$eval('#set-defaultEpSelect option', opts => opts.length);
  check('cards populated with endpoints after early refresh', initialOptionsCount >= 2, String(initialOptionsCount));

  // 3. Close Settings and reopen repeatedly (lifecycle preservation).
  await session.page.evaluate(async () => {
    const src = document.querySelector('script[src*="/static/js/settings.js"]').getAttribute('src');
    const settings = await import(src);
    settings.close();
  });
  await session.page.waitForSelector('#settings-modal.hidden', { state: 'attached' });

  const closedHidden = await session.page.evaluate(() => document.querySelector('#settings-modal').classList.contains('hidden'));
  check('Settings modal closes cleanly', closedHidden);

  // Track write requests to verify no duplicate event handlers fire on repeated opens.
  let saveWrites = 0;
  session.page.on('request', req => {
    if (req.method() === 'POST' && req.url().includes('/api/auth/settings')) {
      saveWrites++;
    }
  });

  // Reopen
  await openSettings(session.page, 'ai');
  await waitForAiDefaults(session.page);

  // Change selection and verify save succeeds with single dispatch.
  saveWrites = 0;
  const firstWrite = session.page.waitForResponse(r => r.url().includes('/api/auth/settings') && r.request().method() === 'POST');
  await session.page.selectOption('#set-defaultEpSelect', 'ep-beta');
  await firstWrite;
  await session.page.waitForFunction(() => document.querySelector('#set-defaultChatMsg').textContent === 'Saved', null, { timeout: 8000 });
  check('single change event triggers exactly one settings write (no listener duplication)', saveWrites === 1, `saveWrites=${saveWrites}`);

  // Re-select alpha to leave state clean.
  saveWrites = 0;
  const secondWrite = session.page.waitForResponse(r => r.url().includes('/api/auth/settings') && r.request().method() === 'POST');
  await session.page.selectOption('#set-defaultEpSelect', 'ep-alpha');
  await secondWrite;
  await session.page.waitForFunction(() => document.querySelector('#set-defaultChatMsg').textContent === 'Saved', null, { timeout: 8000 });
  check('subsequent change event also triggers exactly one write', saveWrites === 1, `saveWrites=${saveWrites}`);

  check('early refresh and repeated open session has no script errors', session.errors.length === 0, session.errors.join(' | '));
  await session.context.close();
}

async function testIntegrationClipboard(instance, cookies) {
  for (const viewport of [{ width: 1280, height: 860 }, { width: 390, height: 844 }]) {
    const { page, context, errors } = await pageAs(instance, cookies.adminCookie, viewport);
    try {
      await context.grantPermissions(['clipboard-read', 'clipboard-write'], { origin: instance.baseUrl });
      await openSettings(page, 'integrations');
      await page.click('#unified-intg-add-btn');
      await page.click('.uf-type-option[data-value="email"]');
      await page.click('#uf-email-provider-trigger');
      await page.click('.ufp-option[data-value="gmail"]');
      const copyLink = page.locator('.uf-prov-copy');
      const [notice] = await Promise.all([
        page.waitForRequest(request => (
          request.method() === 'POST'
          && new URL(request.url()).pathname === '/api/tasks/notification-logs'
          && request.postDataJSON()?.body === 'Copied'
        )),
        copyLink.click(),
      ]);
      const copied = await page.evaluate(() => navigator.clipboard.readText());
      check(`provider URL copies and logs success at ${viewport.width}px`,
        copied === await copyLink.getAttribute('data-url') && notice.postDataJSON().status === 'success');
      await page.click('#uf-email-cancel');

      if (viewport.width > 768) {
        await page.click('#unified-intg-add-btn');
        await page.click('.uf-type-option[data-value="codex"]');
        await page.fill('#uf-codex-name-input', 'Clipboard regression test');
        const [created] = await Promise.all([
          page.waitForResponse(response => new URL(response.url()).pathname === '/api/tokens'
            && response.request().method() === 'POST'),
          page.click('#uf-codex-create-btn'),
        ]);
        check('agent clipboard test creates a disposable minimum-scope token', created.ok()
          && /name="scopes"\r\n\r\nchat\r\n/.test(created.request().postData() || ''));
        await page.waitForSelector('#uf-codex-copy-token');
        await page.evaluate(() => {
          navigator.clipboard.writeText = async () => { throw new Error('Clipboard write denied'); };
          document.execCommand = command => {
            const field = document.activeElement;
            window.clipboardFallbackSelected = command === 'copy'
              && field instanceof HTMLTextAreaElement && field.readOnly
              && field.selectionStart === 0 && field.selectionEnd === field.value.length;
            return false;
          };
        });
        await page.click('#uf-codex-copy-token');
        await page.waitForFunction(() => document.querySelector('#uf-codex-reveal > pre:last-child'));
        check('denied agent clipboard write preserves manual selection and cleans up the temporary field',
          await page.evaluate(() => window.clipboardFallbackSelected
            && !document.querySelector('body > textarea[readonly]')
            && document.querySelector('#uf-codex-token').textContent === window.getSelection().toString()));
      }
      check(`clipboard form interactions have no script errors at ${viewport.width}px`, errors.length === 0, errors.join(' | '));
    } finally {
      await context.close();
    }
  }
}

async function testIntegrationForms(instance, cookies) {
  const admin = await pageAs(instance, cookies.adminCookie);
  await openSettings(admin.page, 'integrations');

  // Verify Add Integration button attributes
  const addBtnAttrs = await admin.page.$eval('#unified-intg-add-btn', btn => ({
    hasPopup: btn.getAttribute('aria-haspopup'),
    expanded: btn.getAttribute('aria-expanded'),
  }));
  check('Add Integration button exposes menu popup and collapsed state', addBtnAttrs.hasPopup === 'menu' && addBtnAttrs.expanded === 'false', JSON.stringify(addBtnAttrs));

  // Open menu
  await admin.page.click('#unified-intg-add-btn');
  const menuAttrs = await admin.page.$eval('.uf-add-menu', menu => ({
    role: menu.getAttribute('role'),
    optionsCount: menu.querySelectorAll('.uf-type-option').length,
    firstRole: menu.querySelector('.uf-type-option')?.getAttribute('role'),
  }));
  check('Add Integration menu has role menu with menuitem options', menuAttrs.role === 'menu' && menuAttrs.optionsCount >= 8 && menuAttrs.firstRole === 'menuitem', JSON.stringify(menuAttrs));

  // Dismiss menu with Escape
  await admin.page.keyboard.press('Escape');
  const menuDismissed = await admin.page.$eval('.uf-add-menu', () => false).catch(() => true);
  check('Escape closes Add Integration menu without closing Settings', menuDismissed);

  // Test every integration form type
  const types = ['api', 'caldav', 'claude', 'codex', 'carddav', 'contacts', 'email', 'mcp'];
  for (const type of types) {
    await admin.page.click('#unified-intg-add-btn');
    await admin.page.click(`.uf-type-option[data-value="${type}"]`);
    await admin.page.waitForFunction(() => {
      const form = document.querySelector('#unified-intg-form');
      return form && getComputedStyle(form).display !== 'none' && form.querySelectorAll('input:not([type="hidden"]), select, button').length > 0 && form.contains(document.activeElement);
    }, null, { timeout: 8000 });

    const formState = await admin.page.evaluate(() => {
      const form = document.querySelector('#unified-intg-form');
      const isVisible = form && getComputedStyle(form).display !== 'none';
      const inputs = Array.from(form.querySelectorAll('input:not([type="hidden"]), select, button'));
      const activeEl = document.activeElement;
      const focusedInside = form.contains(activeEl);
      return {
        isVisible,
        inputsCount: inputs.length,
        focusedInside,
        activeTag: activeEl?.tagName.toLowerCase(),
        activeId: activeEl?.id || activeEl?.className,
      };
    });
    check(`integration form for ${type} opens visibly`, formState.isVisible && formState.inputsCount > 0, JSON.stringify(formState));
    check(`keyboard focus enters the ${type} form on open`, formState.focusedInside, JSON.stringify(formState));

    // Check specific form fields & accessible labels
    if (type === 'api') {
      const apiCheck = await admin.page.evaluate(() => {
        const nameInput = document.querySelector('#uf-api-name');
        const urlInput = document.querySelector('#uf-api-url');
        const nameLabel = document.querySelector('label[for="uf-api-name"]');
        const urlLabel = document.querySelector('label[for="uf-api-url"]');
        return {
          nameLabel: Boolean(nameLabel && nameLabel.textContent.includes('Name')),
          urlLabel: Boolean(urlLabel && urlLabel.textContent.includes('Base URL')),
          nameRequired: nameInput?.hasAttribute('required') || nameInput?.getAttribute('aria-required') === 'true',
          urlRequired: urlInput?.hasAttribute('required') || urlInput?.getAttribute('aria-required') === 'true',
        };
      });
      check('API form controls are labelled and identify required fields', apiCheck.nameLabel && apiCheck.urlLabel && apiCheck.nameRequired && apiCheck.urlRequired, JSON.stringify(apiCheck));
    } else if (type === 'caldav') {
      const caldavCheck = await admin.page.evaluate(() => {
        const urlInput = document.querySelector('#uf-caldav-url');
        const urlLabel = document.querySelector('label[for="uf-caldav-url"]');
        return {
          urlLabel: Boolean(urlLabel && urlLabel.textContent.includes('Server URL')),
          urlRequired: urlInput?.hasAttribute('required') || urlInput?.getAttribute('aria-required') === 'true',
        };
      });
      check('CalDAV form controls are labelled and identify required fields', caldavCheck.urlLabel && caldavCheck.urlRequired, JSON.stringify(caldavCheck));
    } else if (type === 'email') {
      const emailCheck = await admin.page.evaluate(() => {
        const fromInput = document.querySelector('#uf-email-from');
        const fromLabel = document.querySelector('label[for="uf-email-from"]');
        const imapLabel = document.querySelector('label[for="uf-imap-host"]');
        return {
          fromLabel: Boolean(fromLabel && fromLabel.textContent.includes('Email')),
          imapLabel: Boolean(imapLabel && imapLabel.textContent.includes('Host')),
          fromRequired: fromInput?.hasAttribute('required') || fromInput?.getAttribute('aria-required') === 'true',
        };
      });
      check('Email form controls are labelled and identify required fields', emailCheck.fromLabel && emailCheck.imapLabel && emailCheck.fromRequired, JSON.stringify(emailCheck));
    } else if (type === 'mcp') {
      const mcpCheck = await admin.page.evaluate(() => {
        const nameInput = document.querySelector('#uf-mcp-name');
        const nameLabel = document.querySelector('label[for="uf-mcp-name"]');
        return {
          nameLabel: Boolean(nameLabel && nameLabel.textContent.includes('Name')),
          nameRequired: nameInput?.hasAttribute('required') || nameInput?.getAttribute('aria-required') === 'true',
        };
      });
      check('MCP form controls are labelled and identify required fields', mcpCheck.nameLabel && mcpCheck.nameRequired, JSON.stringify(mcpCheck));
    } else if (type === 'claude' || type === 'codex') {
      const agentCheck = await admin.page.evaluate(() => {
        const nameInput = document.querySelector('#uf-codex-name-input');
        return {
          hasAriaLabel: Boolean(nameInput?.getAttribute('aria-label')),
          placeholder: nameInput?.getAttribute('placeholder'),
        };
      });
      check(`${type} agent form has accessible label on name input`, agentCheck.hasAriaLabel && agentCheck.placeholder?.toLowerCase().includes(type), JSON.stringify(agentCheck));
    }

    await screenshot(admin.page, `integration-form-${type}`);

    // Click Cancel
    const cancelSelector = (type === 'claude' || type === 'codex')
      ? '#uf-codex-cancel'
      : (type === 'caldav' ? '#uf-caldav-cancel'
        : type === 'carddav' || type === 'contacts' ? '#uf-carddav-cancel'
        : type === 'email' ? '#uf-email-cancel'
        : type === 'mcp' ? '#uf-mcp-cancel'
        : '#uf-api-cancel');

    await admin.page.click(cancelSelector);
    await admin.page.waitForFunction(() => {
      const form = document.querySelector('#unified-intg-form');
      return form && getComputedStyle(form).display === 'none' && form.innerHTML === '';
    }, null, { timeout: 8000 });

    const closedState = await admin.page.evaluate(() => {
      const form = document.querySelector('#unified-intg-form');
      return {
        displayNone: getComputedStyle(form).display === 'none',
        innerHTML: form.innerHTML,
        activeCardCount: document.querySelectorAll('.intg-card.intg-card-active').length,
      };
    });
    check(`cancelling ${type} form clears form DOM and active card highlight`, closedState.displayNone && closedState.innerHTML === '' && closedState.activeCardCount === 0, JSON.stringify(closedState));
  }

  // Safe save & delete lifecycle using an isolated API integration
  await admin.page.click('#unified-intg-add-btn');
  await admin.page.click('.uf-type-option[data-value="api"]');
  await admin.page.fill('#uf-api-name', 'Phase4 Automated Service');
  await admin.page.fill('#uf-api-url', 'http://127.0.0.1:8080');
  await admin.page.click('#uf-api-save');
  await admin.page.waitForFunction(() => document.querySelector('#uf-api-msg')?.textContent === 'Saved', null, { timeout: 8000 });
  check('saving API integration displays Saved confirmation', true);

  await admin.page.waitForSelector('.intg-card[data-intg-type="api"]');
  const createdCard = await admin.page.$eval('.intg-card[data-intg-type="api"]', card => card.textContent);
  check('saved API integration appears in integrations list', createdCard.includes('Phase4 Automated Service'));

  await admin.page.click('#uf-api-cancel');

  // Test clicking the card to open edit view and verify populated values
  await admin.page.click('.intg-card[data-intg-type="api"]');
  await admin.page.waitForSelector('#uf-api-name');
  await admin.page.waitForFunction(
    () => document.querySelector('#uf-api-name')?.value === 'Phase4 Automated Service'
      && document.querySelector('#uf-api-url')?.value === 'http://127.0.0.1:8080',
    null,
    { timeout: 8000 },
  );
  const editVals = await admin.page.evaluate(() => ({
    name: document.querySelector('#uf-api-name').value,
    url: document.querySelector('#uf-api-url').value,
  }));
  check('editing API integration populates existing values', editVals.name === 'Phase4 Automated Service' && editVals.url === 'http://127.0.0.1:8080', JSON.stringify(editVals));

  // API form hints are plain text: no markup, and title matches aria-label.
  const apiHints = await admin.page.$$eval('#uf-api-name >> xpath=ancestor::*[contains(@class,"admin-card")][1]//*[contains(@class,"uf-hint")]', nodes => nodes.map(n => ({
    title: n.getAttribute('title') || '',
    aria: n.getAttribute('aria-label') || '',
  })));
  check('API form renders its three field hints', apiHints.length === 3, JSON.stringify(apiHints));
  check(
    'API form hint tooltips are plain text matching aria-label',
    apiHints.length > 0 && apiHints.every(h => h.title.length > 20 && h.title === h.aria && !/[<>]/.test(h.title)),
    JSON.stringify(apiHints),
  );
  check(
    'API auth hint keeps its mode descriptions without markup',
    apiHints.some(h => /Bearer = sends/.test(h.title) && /None = the API is open/.test(h.title)),
    JSON.stringify(apiHints.map(h => h.title.slice(0, 80))),
  );

  // Cancel edit view
  await admin.page.click('#uf-api-cancel');

  // Delete the test integration safely
  await admin.page.evaluate(() => {
    window.styledConfirm = async () => true;
  });
  await admin.page.click('.intg-card[data-intg-type="api"] .intg-del-btn');
  await admin.page.waitForSelector('.intg-card[data-intg-type="api"]', { state: 'detached', timeout: 8000 });
  const deletedCard = await admin.page.evaluate(() => Boolean(document.querySelector('.intg-card[data-intg-type="api"]')));
  check('deleting API integration removes it from the list', !deletedCard);

  check('admin integration forms session has no script errors', admin.errors.length === 0, admin.errors.join(' | '));
  await admin.context.close();

  // Mobile layout check for integration form in a dedicated mobile touch context
  const mobile = await pageAs(instance, cookies.adminCookie, { width: 390, height: 844 });
  await openSettings(mobile.page, 'integrations');
  await mobile.page.click('#unified-intg-add-btn');
  await mobile.page.click('.uf-type-option[data-value="api"]');
  await mobile.page.waitForSelector('#unified-intg-form:not([style*="display: none"])', { timeout: 8000 });
  const mobileFormLayout = await mobile.page.evaluate(() => {
    const form = document.querySelector('#unified-intg-form');
    return {
      found: Boolean(form),
      fits: form ? form.scrollWidth <= 390 : false,
      docWidth: document.documentElement.scrollWidth,
      viewportWidth: window.innerWidth,
    };
  });
  check(
    'mobile integration form fits viewport without horizontal overflow',
    mobileFormLayout.found && mobileFormLayout.fits && mobileFormLayout.docWidth <= 390,
    JSON.stringify(mobileFormLayout),
  );
  await screenshot(mobile.page, 'integration-mobile-api-dark');
  check('mobile integration forms session has no script errors', mobile.errors.length === 0, mobile.errors.join(' | '));
  await mobile.context.close();
}

async function testUnknownTabAndRepeatedOpen(instance, cookies) {
  const member = await pageAs(instance, cookies.memberCookie);
  await member.page.evaluate(async () => {
    const src = document.querySelector('script[src*="/static/js/settings.js"]').getAttribute('src');
    (await import(src)).open('completely-unknown-panel-xyz');
  });
  await member.page.waitForSelector('#settings-modal:not(.hidden)');
  const memberLanding = await member.page.$eval('[data-settings-tab].active', btn => btn.dataset.settingsTab);
  check('member open with unknown panel id lands safely on an offered panel', memberLanding === 'ai', memberLanding);

  await member.page.evaluate(async () => {
    const src = document.querySelector('script[src*="/static/js/settings.js"]').getAttribute('src');
    (await import(src)).close();
  });
  await member.page.waitForSelector('#settings-modal.hidden', { state: 'attached' });
  check('closing Settings with close() adds hidden class', true);

  await member.page.evaluate(async () => {
    const src = document.querySelector('script[src*="/static/js/settings.js"]').getAttribute('src');
    (await import(src)).open('appearance');
  });
  await member.page.waitForSelector('#settings-modal:not(.hidden)');
  const appearanceOpen = await member.page.evaluate(() => document.body.classList.contains('settings-appearance-open'));
  check('opening appearance panel activates appearance-open on body', appearanceOpen);

  await member.page.evaluate(async () => {
    const src = document.querySelector('script[src*="/static/js/settings.js"]').getAttribute('src');
    (await import(src)).close();
  });
  await member.page.waitForSelector('#settings-modal.hidden', { state: 'attached' });
  const appearanceClosed = await member.page.evaluate(() => !document.body.classList.contains('settings-appearance-open'));
  check('closing Settings clears appearance-open from body', appearanceClosed);

  for (let i = 0; i < 5; i++) {
    await member.page.evaluate(async () => {
      const src = document.querySelector('script[src*="/static/js/settings.js"]').getAttribute('src');
      const mod = await import(src);
      mod.open('account');
      mod.close();
    });
  }
  check('repeated rapid open and close does not throw or crash', member.errors.length === 0, member.errors.join(' | '));

  await member.context.close();
}

async function checkResponsiveSettings(page, vpName, scheme, errors) {
  const overflowResult = await page.evaluate(() => {
    const content = document.querySelector('.settings-modal-content');
    if (!content) return { found: false, overflow: -1 };
    return { found: true, overflow: content.scrollWidth - content.clientWidth };
  });
  check(
    `responsive settings renders without overflow on ${vpName} (${scheme})`,
    errors.length === 0 && overflowResult.found && overflowResult.overflow <= 1,
    !overflowResult.found
      ? 'missing .settings-modal-content'
      : errors.length
      ? errors.join(' | ')
      : `overflow: ${overflowResult.overflow}px`,
  );
  await screenshot(page, `settings-${vpName}-${scheme}`);
}

async function testAccessibilityAndThemes(instance, cookies) {
  // 1. Representative fresh desktop dark initialization and responsive adaptation
  const desktopDark = await pageAs(instance, cookies.adminCookie, { width: 1280, height: 860 }, 'dark');
  await openSettings(desktopDark.page, 'ai');
  await checkResponsiveSettings(desktopDark.page, 'desktop', 'dark', desktopDark.errors);
  for (const vp of [{ name: 'laptop', width: 1024, height: 768 }, { name: 'tablet', width: 768, height: 1024 }]) {
    await desktopDark.page.setViewportSize({ width: vp.width, height: vp.height });
    await checkResponsiveSettings(desktopDark.page, vp.name, 'dark', desktopDark.errors);
  }
  await desktopDark.context.close();

  // 2. Representative fresh light-mode initialization and responsive adaptation
  const desktopLight = await pageAs(instance, cookies.adminCookie, { width: 1280, height: 860 }, 'light');
  await desktopLight.page.evaluate(() => window.themeModule?.applyColors?.(window.themeModule.THEMES.light));
  await openSettings(desktopLight.page, 'ai');
  await checkResponsiveSettings(desktopLight.page, 'desktop', 'light', desktopLight.errors);
  for (const vp of [{ name: 'laptop', width: 1024, height: 768 }, { name: 'tablet', width: 768, height: 1024 }]) {
    await desktopLight.page.setViewportSize({ width: vp.width, height: vp.height });
    await checkResponsiveSettings(desktopLight.page, vp.name, 'light', desktopLight.errors);
  }
  await desktopLight.context.close();

  // 3. Representative fresh mobile initialization (narrow viewport with hasTouch: true)
  const mobileSession = await pageAs(instance, cookies.adminCookie, { width: 390, height: 844 }, 'dark');
  await openSettings(mobileSession.page, 'ai');
  await checkResponsiveSettings(mobileSession.page, 'mobile', 'dark', mobileSession.errors);
  await mobileSession.page.emulateMedia({ colorScheme: 'light' });
  await mobileSession.page.evaluate(() => window.themeModule?.applyColors?.(window.themeModule.THEMES.light));
  await checkResponsiveSettings(mobileSession.page, 'mobile', 'light', mobileSession.errors);
  await mobileSession.context.close();

  const member = await pageAs(instance, cookies.memberCookie);
  await openSettings(member.page, 'ai');
  await waitForAiDefaults(member.page);

  const lockedA11y = await member.page.evaluate(() => {
    const card = document.querySelector('[data-settings-section="vision"]');
    if (!card) return null;
    const badge = card.querySelector('.settings-scope-badge');
    const note = card.querySelector('.settings-scope-note');
    const subtitle = card.querySelector('.admin-toggle-sub');
    const modelLabel = card.querySelector('label.settings-label');
    const select = card.querySelector('#set-vlModelSelect');
    const toggle = card.querySelector('#set-visionEnabledToggle');

    return {
      cardLocked: card.classList.contains('settings-scope-locked'),
      badgeText: badge?.textContent,
      badgeTitle: badge?.getAttribute('title'),
      noteRole: note?.getAttribute('role'),
      noteText: note?.textContent,
      subtitleInert: subtitle ? Boolean(subtitle.closest('[inert]')) : null,
      modelLabelInert: modelLabel ? Boolean(modelLabel.closest('[inert]')) : null,
      selectDisabled: select?.disabled,
      selectAriaDisabled: select?.getAttribute('aria-disabled'),
      selectInert: select ? Boolean(select.closest('[inert]')) : null,
      toggleInert: toggle ? Boolean(toggle.closest('[inert]')) : null,
    };
  });

  check('locked vision card has explanatory scope badge and note', lockedA11y && lockedA11y.badgeText === 'Set by admin' && lockedA11y.noteRole === 'note' && lockedA11y.noteText.includes('Only an administrator can change this'), JSON.stringify(lockedA11y));
  check('locked card explanatory labels and descriptions are NOT inert (remain accessible)', lockedA11y && lockedA11y.subtitleInert === false && lockedA11y.modelLabelInert === false, JSON.stringify(lockedA11y));
  check('locked card interactive controls are disabled and inert', lockedA11y && lockedA11y.selectDisabled === true && lockedA11y.selectInert === true && lockedA11y.toggleInert === true, JSON.stringify(lockedA11y));

  await member.page.fill('#settings-nav-search', 'chat');
  await member.page.waitForSelector('[data-settings-search-result]');
  const searchA11y = await member.page.evaluate(() => {
    const live = document.querySelector('#settings-nav-search-results .a11y-visually-hidden');
    const results = document.querySelectorAll('[data-settings-search-result]');
    const input = document.querySelector('#settings-nav-search');
    return {
      liveText: live?.textContent,
      liveRole: live?.getAttribute('role'),
      expanded: input.getAttribute('aria-expanded'),
      resultsCount: results.length,
    };
  });
  check('search results announce match count via live status region', searchA11y.liveRole === 'status' && searchA11y.liveText?.includes('available') && searchA11y.resultsCount > 0, JSON.stringify(searchA11y));
  await screenshot(member.page, 'settings-search-results-announced');

  await member.context.close();
}

async function main() {
  await timeScenario('setup: browser launch', async () => {
    browser = await chromium.launch({ headless: true });
  });
  let authInstance = null;
  let cookies = null;
  await timeScenario('setup: auth instance & users', async () => {
    authInstance = await launchInstance('auth', { authEnabled: true });
    seedEndpoints(authInstance);
    cookies = await setupUsers(authInstance);
  });

  let openInstanceError = null;
  const openInstancePromise = (async () => {
    try {
      const inst = await launchInstance('noauth', { authEnabled: false });
      seedEndpoints(inst);
      return inst;
    } catch (err) {
      openInstanceError = err;
      return null;
    }
  })();

  await timeScenario('testFinder', () => testFinder(authInstance, cookies));
  await timeScenario('testMemberLoadFailureAndFallbackCompatibility', () => testMemberLoadFailureAndFallbackCompatibility(authInstance, cookies));
  await timeScenario('testMemberScope', () => testMemberScope(authInstance, cookies));
  await timeScenario('testAdminScope', () => testAdminScope(authInstance, cookies));
  await timeScenario('testPersonalDefaultLabel', () => testPersonalDefaultLabel(authInstance, cookies));
  await timeScenario('testAdminLoadFailure', () => testAdminLoadFailure(authInstance, cookies));
  await timeScenario('testRestrictedNavigation', () => testRestrictedNavigation(authInstance, cookies));
  await timeScenario('testMemberUtilityFallbackRecovery', () => testMemberUtilityFallbackRecovery(authInstance, cookies));
  await timeScenario('testMemberStatusRecovery', () => testMemberStatusRecovery(authInstance, cookies));
  await timeScenario('testDelayedAuthorizationResolution', () => testDelayedAuthorizationResolution(authInstance, cookies));
  await timeScenario('testEarlyEndpointRefreshAndRepeatedOpen', () => testEarlyEndpointRefreshAndRepeatedOpen(authInstance, cookies));
  await timeScenario('testIntegrationForms', () => testIntegrationForms(authInstance, cookies));
  await timeScenario('testIntegrationClipboard', () => testIntegrationClipboard(authInstance, cookies));
  await timeScenario('testIntegrationsAuthRecovery (admin)', () => testIntegrationsAuthRecovery(authInstance, cookies, { admin: true }));
  await timeScenario('testIntegrationsAuthRecovery (member)', () => testIntegrationsAuthRecovery(authInstance, cookies, { admin: false }));
  await timeScenario('testIntegrationsRapidReopenDuringRecovery', () => testIntegrationsRapidReopenDuringRecovery(authInstance, cookies));
  await timeScenario('testIntegrationsIgnoreStaleListResponse', () => testIntegrationsIgnoreStaleListResponse(authInstance, cookies));
  await timeScenario('testUnknownTabAndRepeatedOpen', () => testUnknownTabAndRepeatedOpen(authInstance, cookies));
  await timeScenario('testAccessibilityAndThemes', () => testAccessibilityAndThemes(authInstance, cookies));

  let openInstance = null;
  await timeScenario('setup: noauth instance', async () => {
    openInstance = await openInstancePromise;
    if (openInstanceError) throw openInstanceError;
  });
  await timeScenario('testAuthDisabledOperator', () => testAuthDisabledOperator(openInstance));
  await timeScenario('testIntegrationsAuthRecovery (noauth operator)', () => testIntegrationsAuthRecovery(openInstance, {}, { admin: true, authDisabled: true }));

  if (PROFILE && scenarioTimings.length > 0) {
    console.log('\n================ SCENARIO TIMING BREAKDOWN ================');
    console.log('Scenario | Duration (ms)');
    console.log('---|---');
    for (const s of scenarioTimings) {
      console.log(`${s.name} | ${s.durationMs}`);
    }
    const totalDuration = scenarioTimings.reduce((sum, s) => sum + s.durationMs, 0);
    console.log('---|---');
    console.log(`TOTAL | ${totalDuration}`);
    console.log('===========================================================\n');
  }

  console.log(`\n401/403 responses seen per persona: ${JSON.stringify(forbiddenReport)}`);
  const failed = results.filter(result => !result.pass);
  console.log(`\n${results.length - failed.length}/${results.length} browser checks passed`);
  if (failed.length) {
    for (const instance of instances) console.log(`--- ${instance.name} server log tail ---\n${instance.log().slice(-3000)}`);
    process.exitCode = 1;
  }
}

main()
  .catch(error => {
    console.error(error);
    for (const instance of instances) console.error(`--- ${instance.name} server log tail ---\n${instance.log().slice(-3000)}`);
    process.exitCode = 1;
  })
  .finally(stopAll);
