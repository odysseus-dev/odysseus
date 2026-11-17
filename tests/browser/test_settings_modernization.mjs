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

async function waitFor(url, timeoutMs = 40000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const response = await fetch(url);
      if (response.status < 500) return;
    } catch (_) {}
    await new Promise(r => setTimeout(r, 300));
  }
  throw new Error(`Server did not start at ${url}`);
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
  await waitFor(`${instance.baseUrl}/api/health`);
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
  execFileSync('sqlite3', [instance.dbPath], { input: sql });
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

async function pageAs(instance, cookie, viewport = { width: 1280, height: 860 }, colorScheme = 'dark') {
  const context = await browser.newContext({ viewport, colorScheme });
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
  await page.goto(`${instance.baseUrl}/`, { waitUntil: 'domcontentloaded' });
  await page.waitForFunction(() => window._isAdmin !== undefined, null, { timeout: 15000 });
  await page.waitForTimeout(400);
  return { page, context, errors, forbidden, settingsWrites };
}

async function openSettings(page, tab) {
  if (await page.isVisible('#user-bar-settings')) {
    await page.click('#user-bar-settings');
  } else {
    // Narrow layouts hide the sidebar; use the app's own Settings module.
    await page.evaluate(async () => {
      const src = document.querySelector('script[src*="/static/js/settings.js"]').getAttribute('src');
      (await import(src)).open();
    });
  }
  await page.waitForSelector('#settings-modal:not(.hidden)');
  if (tab) await page.click(`[data-settings-tab="${tab}"]`);
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
  await member.page.waitForTimeout(600);
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
  await member.page.waitForTimeout(800);
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
    await new Promise(r => setTimeout(r, 700));
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
  await admin.page.waitForTimeout(600);
  const adminReveal = await admin.page.evaluate(() => ({
    activeTab: document.querySelector('[data-settings-tab].active')?.dataset.settingsTab,
    focused: document.activeElement?.dataset?.settingsSection,
  }));
  check('admin-managed panel result routes through the admin controller and reveals the card', adminReveal.activeTab === 'system' && adminReveal.focused === 'backup', JSON.stringify(adminReveal));
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
    return select && Array.from(select.options).some(option => option.value === 'ep-alpha');
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
  await member.page.waitForTimeout(400);
  const beforeMove = (await api(instance, cookies.memberCookie, '/api/prefs')).body.foreground_model_fallbacks || [];
  await member.page.click('#set-foregroundFallbacks .settings-fallback-row:nth-child(2) [data-move-offset="-1"]');
  await member.page.waitForTimeout(600);
  const prefsAfterMove = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
  const afterMove = prefsAfterMove.foreground_model_fallbacks || [];
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
  await member.page.waitForTimeout(800);
  const aiLocks = await lockedControlsOutsideInert(member.page);
  check('every control in locked AI cards is inert after load', aiLocks.lockedCards >= 2 && aiLocks.leaks.length === 0, JSON.stringify(aiLocks));
  for (const tab of ['search', 'reminders']) {
    await member.page.click(`[data-settings-tab="${tab}"]`);
    await member.page.waitForTimeout(800);
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

  // Clearing returns the account to the server default.
  await member.page.selectOption('#set-defaultEpSelect', '');
  await member.page.waitForFunction(() => document.querySelector('#set-defaultChatMsg').textContent === 'Saved', null, { timeout: 8000 });
  const cleared = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
  check('choosing "Not set" removes the personal default', !('default_endpoint_id' in cleared) && !('default_model' in cleared), JSON.stringify(cleared));

  // Utility personal save also stays personal.
  await member.page.selectOption('#set-utilityEpSelect', 'ep-alpha');
  await member.page.waitForFunction(() => document.querySelector('#set-utilityChatMsg').textContent === 'Saved', null, { timeout: 8000 });
  await member.page.selectOption('#set-utilityModelSelect', 'alpha-large');
  await member.page.waitForFunction(async () => {
    const prefs = await (await fetch('/api/prefs', { credentials: 'same-origin' })).json();
    return prefs.utility_model === 'alpha-large';
  }, null, { timeout: 8000 }).catch(() => {});
  const utilityPrefs = (await api(instance, cookies.memberCookie, '/api/prefs')).body;
  check('member utility model is stored per account', utilityPrefs.utility_endpoint_id === 'ep-alpha' && utilityPrefs.utility_model === 'alpha-large', JSON.stringify(utilityPrefs));

  // Light theme and mobile rendering of the scoped cards.
  const light = await pageAs(instance, cookies.memberCookie, { width: 1280, height: 860 }, 'light');
  // Apply the built-in light preset colors through the theme module.
  await light.page.evaluate(() => window.themeModule.applyColors(window.themeModule.THEMES.light));
  await openSettings(light.page, 'ai');
  await waitForAiDefaults(light.page);
  await screenshot(light.page, 'scope-member-ai-light');
  await light.context.close();
  const mobile = await pageAs(instance, cookies.memberCookie, { width: 390, height: 844 });
  await openSettings(mobile.page, 'ai');
  await waitForAiDefaults(mobile.page);
  const overflow = await mobile.page.evaluate(() => {
    const content = document.querySelector('.settings-modal-content');
    return content.scrollWidth - content.clientWidth;
  });
  check('mobile Settings has no horizontal overflow with scope badges', overflow <= 1, String(overflow));
  await screenshot(mobile.page, 'scope-member-ai-mobile');
  check('member scope pages have no script errors', member.errors.length === 0 && mobile.errors.length === 0, [...member.errors, ...mobile.errors].join(' | '));
  check('member session never posts server-wide settings', member.settingsWrites.length === 0 && mobile.settingsWrites.length === 0, [...member.settingsWrites, ...mobile.settingsWrites].join(','));
  forbiddenReport.member = [...new Set([...member.forbidden, ...mobile.forbidden])];
  await mobile.context.close();
  await member.context.close();
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

  await admin.page.selectOption('#set-defaultEpSelect', 'ep-alpha');
  await admin.page.waitForFunction(() => document.querySelector('#set-defaultChatMsg').textContent === 'Saved', null, { timeout: 8000 });
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
  await operator.page.click('[data-settings-tab="ai"]');
  await operator.page.waitForFunction(() => Array.from(document.querySelector('#set-defaultEpSelect').options).some(o => o.value === 'ep-alpha'), null, { timeout: 15000 });
  await operator.page.waitForTimeout(300);
  const badges = await operator.page.$$eval('.settings-scope-badge', nodes => nodes.length);
  check('single-user install shows no per-user scope labels', badges === 0, String(badges));
  await operator.page.selectOption('#set-defaultEpSelect', 'ep-beta');
  await operator.page.waitForFunction(() => document.querySelector('#set-defaultChatMsg').textContent === 'Saved', null, { timeout: 8000 });
  const settings = (await api(instance, null, '/api/auth/settings')).body;
  check('auth-disabled operator saves the server-wide default', settings.default_endpoint_id === 'ep-beta', JSON.stringify({ ep: settings.default_endpoint_id }));
  await operator.page.click('[data-settings-tab="added-models"]');
  await operator.page.waitForTimeout(500);
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
  await operator.page.click('[data-settings-tab="system"]');
  await operator.page.waitForTimeout(800);
  const system = await operator.page.evaluate(() => ({
    backupShown: getComputedStyle(document.querySelector('[data-settings-section="backup"]')).display !== 'none',
    dangerShown: getComputedStyle(document.querySelector('[data-settings-section="danger-zone"]')).display !== 'none',
  }));
  check('auth-disabled operator System panel shows backup and danger zone', system.backupShown && system.dangerShown, JSON.stringify(system));
  forbiddenReport.operator = [...new Set(operator.forbidden)];
  check('operator page has no script errors', operator.errors.length === 0, operator.errors.join(' | '));
  await operator.context.close();
}

async function main() {
  browser = await chromium.launch({ headless: true });
  const authInstance = await launchInstance('auth', { authEnabled: true });
  seedEndpoints(authInstance);
  const cookies = await setupUsers(authInstance);

  await testFinder(authInstance, cookies);
  await testMemberLoadFailureAndFallbackCompatibility(authInstance, cookies);
  await testMemberScope(authInstance, cookies);
  await testAdminScope(authInstance, cookies);

  const openInstance = await launchInstance('noauth', { authEnabled: false });
  seedEndpoints(openInstance);
  await testAuthDisabledOperator(openInstance);

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
    process.exitCode = 1;
  })
  .finally(stopAll);
