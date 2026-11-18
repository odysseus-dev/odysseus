// Real-browser proof that Settings opens offline after a service-worker upgrade.
//
//   node tests/browser/test_settings_offline_upgrade.mjs
//   python -m pytest tests/test_settings_offline_upgrade_browser.py
//
// The application version being upgraded from is pinned: e195177f is the dev
// commit the Settings decomposition was branched from. It has the previous
// worker (cache v1149) and none of the new Settings modules, and it stays
// reachable from dev, so the test means the same thing before and after the
// decomposition merges. A moving ref such as origin/dev would not.
//
// Environment (all optional):
//   PREVIOUS_REF   override the pinned commit for ad-hoc experiments.
//   ARTIFACT_DIR   directory for a screenshot of the offline Settings panel.
//
// Procedure
//   1. Run the PREVIOUS application from a git archive of the pinned commit in a
//      fresh browser profile. The app registers its own worker; wait until
//      that worker is activated and its cache exists.
//   2. Close the browser (the profile now looks like an existing install),
//      stop the previous server and start the CANDIDATE application (this
//      working tree) on the same origin.
//   3. Reopen the same profile and let the browser find the new worker. Wait
//      for it to install and activate and for the previous cache to be
//      removed. No Settings module is requested by any page in this step.
//   4. Clear the ordinary HTTP cache (CacheStorage is left alone), stop the
//      candidate server and switch the browser offline.
//   5. From a page the worker controls, import Settings, open it and visit
//      every offered panel. Every module must come from the worker's cache.
//
// The worker is registered at /static/ and so does not control the SPA at /.
// Step 1 asserts that, which is why steps 3 and 5 use an in-scope page: an
// offline reload of / is outside what this worker can serve.
//
// Isolated data directories and ephemeral ports; nothing here contacts a real
// account, provider or integration.

import { spawn, spawnSync, execFileSync } from 'child_process';
import { chromium } from 'playwright';
import { mkdirSync, mkdtempSync, rmSync, existsSync, readFileSync } from 'fs';
import { join, resolve } from 'path';
import { tmpdir, homedir } from 'os';
import { fileURLToPath } from 'url';
import net from 'net';

const REPO_ROOT = resolve(process.env.REPO_ROOT || join(fileURLToPath(import.meta.url), '..', '..', '..'));
const PINNED_PREVIOUS_REVISION = 'e195177ffe7427af30402fcaada715d2e5e35918';
const PREVIOUS_REF = process.env.PREVIOUS_REF || PINNED_PREVIOUS_REVISION;
const ARTIFACT_DIR = process.env.ARTIFACT_DIR || null;

const results = [];
const servers = [];
const scratch = [];
let context = null;

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

async function waitFor(url, timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      if ((await fetch(url)).status < 500) return;
    } catch (_) {}
    await new Promise(r => setTimeout(r, 300));
  }
  throw new Error(`Server did not start at ${url}`);
}

function tempDir(label) {
  const dir = mkdtempSync(join(tmpdir(), `odysseus-${label}-`));
  scratch.push(dir);
  return dir;
}

function extractPrevious() {
  const dir = tempDir('previous-app');
  const archive = spawnSync('git', ['archive', PREVIOUS_REF], { cwd: REPO_ROOT, maxBuffer: 1 << 30 });
  if (archive.status !== 0) throw new Error(`cannot archive ${PREVIOUS_REF}: ${archive.stderr}`);
  execFileSync('tar', ['-x', '-C', dir], { input: archive.stdout, maxBuffer: 1 << 30 });
  return dir;
}

async function startApp(name, appRoot, port) {
  const root = tempDir(`${name}-data`);
  for (const sub of ['data', 'fastembed', 'mail', 'runtime']) mkdirSync(join(root, sub), { recursive: true });
  const env = {
    ...process.env,
    ODYSSEUS_DATA_DIR: join(root, 'data'),
    DATABASE_URL: `sqlite:///${join(root, 'test.db')}`,
    AUTH_ENABLED: 'false',
    LOCALHOST_BYPASS: 'false',
    PORT: String(port),
    ODYSSEUS_TOOL_INDEX_PREWARM: '0',
    ODYSSEUS_STARTUP_WARMUPS: '0',
    ODYSSEUS_MODEL_KEEPALIVE: '0',
    ODYSSEUS_DISABLE_MCP: '1',
    CHROMADB_HOST: '127.0.0.1',
    CHROMADB_PORT: String(await freePort()),
    FASTEMBED_CACHE_PATH: join(root, 'fastembed'),
    ODYSSEUS_MAIL_ATTACHMENTS_DIR: join(root, 'mail'),
    XDG_RUNTIME_DIR: join(root, 'runtime'),
    RESPONSE_COMPRESSION_ENABLED: 'false',
    PYTHONPATH: appRoot,
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
    { env, cwd: appRoot, detached: true, stdio: ['ignore', 'pipe', 'pipe'] },
  );
  let log = '';
  proc.stdout.on('data', d => { log += d; });
  proc.stderr.on('data', d => { log += d; });
  const exited = new Promise(done => proc.once('exit', done));
  const server = { name, proc, exited, log: () => log };
  servers.push(server);
  await waitFor(`http://127.0.0.1:${port}/api/health`);
  return server;
}

async function stopApp(server) {
  try { process.kill(-server.proc.pid, 'SIGKILL'); } catch (_) {
    try { server.proc.kill('SIGKILL'); } catch (_) {}
  }
  await Promise.race([server.exited, new Promise(r => setTimeout(r, 5000))]);
}

function cleanup() {
  for (const server of servers.splice(0)) {
    try { process.kill(-server.proc.pid, 'SIGKILL'); } catch (_) {}
  }
  for (const dir of scratch.splice(0)) {
    if (existsSync(dir)) rmSync(dir, { recursive: true, force: true });
  }
}
process.on('exit', cleanup);
process.on('SIGINT', () => { cleanup(); process.exit(130); });

// What a given application version says about its own worker.
function describeVersion(appRoot) {
  const sw = readFileSync(join(appRoot, 'static', 'sw.js'), 'utf8');
  const index = readFileSync(join(appRoot, 'static', 'index.html'), 'utf8');
  const lists = ['PRECACHE', 'PANEL_PRECACHE'].flatMap(name => {
    const block = sw.match(new RegExp(`const ${name} = \\[([\\s\\S]*?)\\n\\];`));
    return block ? [...block[1].matchAll(/^\s+'(\/[^']+)',?$/gm)].map(match => match[1]) : [];
  });
  return {
    cacheName: sw.match(/const CACHE_NAME = '([^']+)'/)[1],
    registration: index.match(/serviceWorker\.register\('([^']+)'\)/)[1],
    settingsEntry: lists.find(url => url.startsWith('/static/js/settings.js?')),
    probePage: lists.find(url => url.startsWith('/static/js/panels.js')),
    precache: new Set(lists),
  };
}

// page.waitForFunction does not await an async predicate, so poll explicitly.
async function until(page, predicate, argument, timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await page.evaluate(predicate, argument)) return;
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error(`timed out waiting for: ${predicate}`);
}

// Lazy imports that were already absent from the precache before the Settings
// decomposition (settings.js imported the same URL on origin/dev). They sit
// outside the Settings module graph; any other uncached module fails the test.
const KNOWN_LAZY_GAPS = new Set(['/static/js/emailLibrary.js?v=20260915trashmove2']);

const MODULE_LOAD_FAILURE = /Failed to fetch dynamically imported module|Failed to load module script|Failed to resolve module specifier|error loading dynamically imported module/i;

async function main() {
  const previousRoot = extractPrevious();
  const previous = describeVersion(previousRoot);
  const candidate = describeVersion(REPO_ROOT);
  check('versions differ in cache name', previous.cacheName !== candidate.cacheName, `${previous.cacheName} vs ${candidate.cacheName}`);
  check('the previous version predates the new Settings modules', !existsSync(join(previousRoot, 'static/js/settings/access.js')) && !previous.precache.has('/static/js/settings/access.js'));

  const port = await freePort();
  const origin = `http://127.0.0.1:${port}`;
  const profile = tempDir('profile');
  const launch = () => chromium.launchPersistentContext(profile, {
    headless: true,
    serviceWorkers: 'allow',
    viewport: { width: 1280, height: 860 },
    args: ['--disable-dev-shm-usage', '--no-sandbox'],
  });

  // 1. Previous application, with its own worker.
  const previousServer = await startApp('previous', previousRoot, port);
  context = await launch();
  let page = await context.newPage();
  await page.goto(`${origin}/`, { waitUntil: 'domcontentloaded' });
  // `serviceWorker.ready` never settles for a page outside the worker's scope.
  await until(page, async () => (await navigator.serviceWorker.getRegistrations()).length > 0, null, 30000);
  const root = await page.evaluate(async () => {
    return {
      controller: Boolean(navigator.serviceWorker.controller),
      rootRegistration: Boolean(await navigator.serviceWorker.getRegistration('/')),
      scopes: (await navigator.serviceWorker.getRegistrations()).map(reg => new URL(reg.scope).pathname),
    };
  });
  check('the app registers its worker at /static/ and does not control the SPA root', root.scopes.join() === '/static/' && !root.controller && !root.rootRegistration, JSON.stringify(root));
  await page.goto(`${origin}${previous.probePage}`, { waitUntil: 'domcontentloaded' });
  await until(page, async () => {
    const registration = (await navigator.serviceWorker.getRegistrations())[0];
    return registration?.active?.state === 'activated' && !registration.installing && !registration.waiting;
  });
  const before = await page.evaluate(async () => ({ caches: await caches.keys(), regs: (await navigator.serviceWorker.getRegistrations()).map(reg => ({ scope: reg.scope, active: reg.active?.state, installing: reg.installing?.state, waiting: reg.waiting?.state })), controller: navigator.serviceWorker.controller?.state }));
  before.state = before.regs[0]?.active;
  check('previous worker is active with its own cache', before.state === 'activated' && before.caches.join() === previous.cacheName, JSON.stringify(before));
  await context.close();
  context = null;
  await stopApp(previousServer);

  // 2. Candidate application on the same origin, same profile.
  await startApp('candidate', REPO_ROOT, port);
  context = await launch();
  const settingsRequests = [];
  const workerWarnings = [];
  context.on('request', request => {
    if (/\/static\/js\/settings(\/|\.js)/.test(new URL(request.url()).pathname) && !request.serviceWorker()) {
      settingsRequests.push(`${request.method()} ${new URL(request.url()).pathname}`);
    }
  });
  context.on('serviceworker', worker => worker.on('console', message => {
    if (message.type() === 'warning') workerWarnings.push(message.text());
  }));
  page = await context.newPage();
  await page.goto(`${origin}${candidate.probePage}`, { waitUntil: 'domcontentloaded' });

  // 3. Let the browser find, install and activate the candidate worker.
  const registrations = await page.evaluate(async () => (await navigator.serviceWorker.getRegistrations()).map(reg => ({ scope: new URL(reg.scope).pathname, active: reg.active?.state || null })));
  check('the existing installation keeps its registration across the browser restart', registrations.length === 1 && registrations[0].active === 'activated', JSON.stringify(registrations));
  await page.evaluate(async () => { await (await navigator.serviceWorker.getRegistrations())[0].update(); });
  await until(page, async name => (await caches.keys()).join() === name, candidate.cacheName);
  await until(page, async () => {
    const registration = (await navigator.serviceWorker.getRegistrations())[0];
    return registration.active?.state === 'activated' && !registration.installing && !registration.waiting;
  });
  const installed = await page.evaluate(async name => {
    const cache = await caches.open(name);
    return { keys: (await cache.keys()).map(request => new URL(request.url).pathname + new URL(request.url).search), all: await caches.keys() };
  }, candidate.cacheName);
  check('upgrade installed and activated; the previous cache was removed', installed.all.join() === candidate.cacheName, JSON.stringify(installed.all));
  const wanted = [...candidate.precache].filter(url => url !== '/');
  const notCached = wanted.filter(url => !installed.keys.includes(url));
  check('every manifest URL is in CacheStorage after the upgrade', notCached.length === 0, `${notCached.length} missing: ${notCached.slice(0, 8).join(', ')}`);
  check('worker install reported no uncached URL', workerWarnings.length === 0, workerWarnings.join(' | '));
  check('no page requested a Settings module before the offline phase', settingsRequests.length === 0, settingsRequests.join(', '));

  // The shell markup is read here, from outside the browser, so nothing in the
  // browser's own caches ever holds a Settings module.
  const indexHtml = await (await fetch(`${origin}/`)).text();

  // 4. No HTTP cache, no server, no network.
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.clearBrowserCache');
  await page.close();
  const candidateServer = servers[servers.length - 1];
  await stopApp(candidateServer);
  await context.setOffline(true);
  const stillUp = await fetch(`${origin}/api/health`).then(() => true, () => false);
  check('the application server is unreachable', !stillUp);

  // 5. Settings, offline, from an in-scope page.
  const offline = await context.newPage();
  const failed = [];
  const consoleErrors = [];
  const pageErrors = [];
  offline.on('requestfailed', request => failed.push(new URL(request.url()).pathname + new URL(request.url()).search));
  offline.on('console', message => { if (message.type() === 'error') consoleErrors.push(message.text()); });
  offline.on('pageerror', error => pageErrors.push(error.message));
  await offline.goto(`${origin}${candidate.probePage}`, { waitUntil: 'domcontentloaded' });
  const controlled = await offline.evaluate(() => Boolean(navigator.serviceWorker.controller));
  check('the offline page is controlled by the upgraded worker', controlled);

  const outcome = await offline.evaluate(async ({ html, entry }) => {
    const shell = new DOMParser().parseFromString(html, 'text/html').getElementById('settings-modal');
    if (!shell) return { error: 'settings-modal missing from the application shell' };
    document.body.replaceChildren(document.adoptNode(shell));
    let settings;
    try {
      settings = await import(entry);
    } catch (error) {
      return { error: `import failed: ${error && error.message}` };
    }
    settings.default.open('ai');
    await new Promise(done => setTimeout(done, 800));
    const modal = document.getElementById('settings-modal');
    const visited = [];
    for (const tab of modal.querySelectorAll('[data-settings-tab]')) {
      if (getComputedStyle(tab).display === 'none' || tab.closest('[hidden]')) continue;
      tab.click();
      await new Promise(done => setTimeout(done, 150));
      visited.push({ tab: tab.dataset.settingsTab, active: modal.querySelector('[data-settings-tab].active')?.dataset.settingsTab });
    }
    const loaded = performance.getEntriesByType('resource').map(entry => new URL(entry.name).pathname + new URL(entry.name).search);
    return { exports: Object.keys(settings.default).sort(), visited, loaded, opened: !modal.classList.contains('hidden') };
  }, { html: indexHtml, entry: candidate.settingsEntry });

  check('Settings imports offline with its complete module graph', !outcome.error && outcome.exports?.includes('open'), outcome.error || JSON.stringify(outcome.exports));
  const navigable = (outcome.visited || []).filter(step => step.tab === step.active);
  check('Settings opens offline and every offered panel can be visited', outcome.opened && navigable.length >= 5 && navigable.length === (outcome.visited || []).length, JSON.stringify(outcome.visited));
  const loadedModules = (outcome.loaded || []).filter(url => url.startsWith('/static/js/'));
  const uncached = loadedModules.filter(url => !installed.keys.includes(url));
  const unexpected = uncached.filter(url => !KNOWN_LAZY_GAPS.has(url));
  check('every module the page loaded was already in the worker cache', loadedModules.length > 60 && unexpected.length === 0, `${loadedModules.length} loaded; not cached: ${unexpected.join(', ')}`);
  const failedStatic = failed.filter(url => url.startsWith('/static/'));
  const unexpectedFailures = failedStatic.filter(url => !KNOWN_LAZY_GAPS.has(url));
  check('no static request failed offline except the known lazy gap', unexpectedFailures.length === 0, unexpectedFailures.join(', '));
  const settingsFailures = failedStatic.filter(url => /^\/static\/js\/settings(\/|\.js)/.test(url));
  check('no Settings module request failed offline', settingsFailures.length === 0, settingsFailures.join(', '));
  if (uncached.length) console.log(`INFO known lazy imports not in the precache (pre-existing): ${uncached.join(', ')}`);
  const loadFailures = [...consoleErrors, ...pageErrors].filter(text => MODULE_LOAD_FAILURE.test(text));
  check('no module load error was reported', loadFailures.length === 0, loadFailures.join(' | '));
  const otherErrors = [...consoleErrors, ...pageErrors].filter(text => !MODULE_LOAD_FAILURE.test(text));
  console.log(`INFO ${otherErrors.length} non-module console/page errors while offline (expected: API calls cannot succeed)`);
  if (ARTIFACT_DIR) {
    mkdirSync(ARTIFACT_DIR, { recursive: true });
    await offline.screenshot({ path: join(ARTIFACT_DIR, 'settings-offline-after-upgrade.png') });
  }
  console.log(`INFO modules loaded offline: ${loadedModules.length}; cache entries: ${installed.keys.length}`);
}

main()
  .catch(error => {
    console.error(error);
    check('the upgrade test ran to completion', false, String(error && error.message));
  })
  .finally(async () => {
    if (context) await context.close().catch(() => {});
    const failed = results.filter(result => !result.pass);
    console.log(`\n${results.length - failed.length}/${results.length} offline-upgrade checks passed`);
    if (failed.length) {
      for (const server of servers) console.log(`--- ${server.name} server log tail ---\n${server.log().slice(-2000)}`);
      process.exitCode = 1;
    }
    cleanup();
  });
