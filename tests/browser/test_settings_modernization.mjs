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
  return { page, context, errors };
}

async function openSettings(page, tab) {
  await page.click('#user-bar-settings');
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

async function main() {
  browser = await chromium.launch({ headless: true });
  const authInstance = await launchInstance('auth', { authEnabled: true });
  seedEndpoints(authInstance);
  const cookies = await setupUsers(authInstance);

  await testFinder(authInstance, cookies);

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
