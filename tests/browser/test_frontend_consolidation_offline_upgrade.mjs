// Real-browser proof of service worker upgrade and offline functionality
// from deployed base (360f6e9b1) to candidate (refactor/frontend-shared-consolidation).
//
// Procedure:
//   1. Run deployed BASE application from git archive (reads baseline cache name).
//   2. Register service worker and confirm base cache is active and populated.
//   3. Close browser and switch server to CANDIDATE application (this worktree) on the same origin.
//   4. Reopen browser, let browser discover updated worker (candidate cache name).
//   5. Confirm new worker installs and activates, and baseline cache is evicted.
//   6. Clear HTTP cache, stop server, go offline.
//   7. Reload/import offline: verify updated entry points and newly consolidated shared modules
//      load together from CacheStorage without errors.

import { spawn, spawnSync, execFileSync } from 'child_process';
import { chromium } from 'playwright';
import { mkdirSync, mkdtempSync, rmSync, existsSync, readFileSync } from 'fs';
import { join, resolve } from 'path';
import { fileURLToPath } from 'url';
import net from 'net';

import { tmpdir, homedir } from 'os';

const REPO_ROOT = resolve(join(fileURLToPath(import.meta.url), '..', '..', '..'));
const CANDIDATE_ROOT = process.env.CANDIDATE_ROOT || REPO_ROOT;
const BASE_REF = process.env.BASE_REF || '360f6e9b112c5c28e2f1c6197f9a25d070ac1a7e';
const SCRATCH_BASE = process.env.TMPDIR || tmpdir();
const CACHE_NAME_PATTERN = /^odysseus-v\d+-pr40-editor-precache$/;

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
    process.env.ODYSSEUS_TEST_PYTHON,
    process.env.PYTHON,
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
  mkdirSync(SCRATCH_BASE, { recursive: true });
  const dir = mkdtempSync(join(SCRATCH_BASE, `odysseus-offline-${label}-`));
  scratch.push(dir);
  return dir;
}

function extractBase() {
  const dir = tempDir('base-app');
  const archive = spawnSync('git', ['archive', BASE_REF], { cwd: REPO_ROOT, maxBuffer: 1 << 30 });
  if (archive.status !== 0) throw new Error(`cannot archive ${BASE_REF}: ${archive.stderr}`);
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

function getCacheName(appRoot) {
  const sw = readFileSync(join(appRoot, 'static', 'sw.js'), 'utf8');
  return sw.match(/const CACHE_NAME = '([^']+)'/)[1];
}

async function until(page, predicate, argument, timeoutMs = 60000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    if (await page.evaluate(predicate, argument)) return;
    await new Promise(r => setTimeout(r, 250));
  }
  throw new Error(`timed out waiting for: ${predicate}`);
}

async function main() {
  console.log(`Extracting base commit ${BASE_REF}...`);
  const baseRoot = extractBase();
  const baseCacheName = getCacheName(baseRoot);
  const candidateCacheName = getCacheName(CANDIDATE_ROOT);

  check('base cache name follows repository convention', CACHE_NAME_PATTERN.test(baseCacheName), baseCacheName);
  check('candidate cache name follows repository convention', CACHE_NAME_PATTERN.test(candidateCacheName), candidateCacheName);
  check('candidate version differs from baseline', candidateCacheName !== baseCacheName, `${candidateCacheName} vs ${baseCacheName}`);

  const port = await freePort();
  const origin = `http://127.0.0.1:${port}`;
  const profile = tempDir('browser-profile');
  const probePage = '/static/js/panels.js?v=20260909movepicklayer1';

  const launch = () => chromium.launchPersistentContext(profile, {
    headless: true,
    serviceWorkers: 'allow',
    viewport: { width: 1280, height: 860 },
    args: ['--disable-dev-shm-usage', '--no-sandbox'],
  });

  // Step 1: Run deployed base app and populate baseline cache
  console.log(`Starting base app server on port ${port}...`);
  const baseServer = await startApp('base', baseRoot, port);
  context = await launch();
  let page = await context.newPage();
  await page.goto(`${origin}/`, { waitUntil: 'domcontentloaded' });
  await until(page, async () => (await navigator.serviceWorker.getRegistrations()).length > 0, null, 30000);
  await page.goto(`${origin}${probePage}`, { waitUntil: 'domcontentloaded' });

  await until(page, async () => {
    const registration = (await navigator.serviceWorker.getRegistrations())[0];
    return registration?.active?.state === 'activated' && !registration.installing && !registration.waiting;
  });

  const baseCaches = await page.evaluate(async () => await caches.keys());
  check('base worker activated and cached with baseline cache', baseCaches.includes(baseCacheName), JSON.stringify(baseCaches));

  await context.close();
  context = null;
  await stopApp(baseServer);
  console.log('Base app stopped. Starting candidate app server on the same port...');

  // Step 2: Run candidate app server and upgrade service worker
  await startApp('candidate', CANDIDATE_ROOT, port);
  context = await launch();
  page = await context.newPage();
  await page.goto(`${origin}${probePage}`, { waitUntil: 'domcontentloaded' });

  // Browser finds candidate worker and upgrades
  await page.evaluate(async () => {
    const reg = (await navigator.serviceWorker.getRegistrations())[0];
    if (reg) await reg.update();
  });

  // Wait for new worker to activate and old cache to be removed
  await until(page, async ({ candidateName, baseName }) => {
    const keys = await caches.keys();
    return keys.includes(candidateName) && !keys.includes(baseName);
  }, { candidateName: candidateCacheName, baseName: baseCacheName });

  await until(page, async () => {
    const reg = (await navigator.serviceWorker.getRegistrations())[0];
    return reg?.active?.state === 'activated' && !reg.installing && !reg.waiting;
  });

  const upgradedCaches = await page.evaluate(async () => await caches.keys());
  check('candidate worker activated and base cache evicted',
    upgradedCaches.includes(candidateCacheName) && !upgradedCaches.includes(baseCacheName),
    JSON.stringify(upgradedCaches)
  );

  // Step 3: Clear HTTP cache, stop server, go offline
  const cdp = await context.newCDPSession(page);
  await cdp.send('Network.clearBrowserCache');
  await page.close();

  const candidateServer = servers[servers.length - 1];
  await stopApp(candidateServer);
  await context.setOffline(true);

  const stillUp = await fetch(`${origin}/api/health`).then(() => true, () => false);
  check('server is offline and unreachable', !stillUp);

  // Step 4: Open offline page and test entry points and shared modules
  const offlinePage = await context.newPage();
  const failedRequests = [];
  const consoleErrors = [];
  const pageErrors = [];

  offlinePage.on('requestfailed', req => failedRequests.push(req.url()));
  offlinePage.on('console', msg => { if (msg.type() === 'error') consoleErrors.push(msg.text()); });
  offlinePage.on('pageerror', err => pageErrors.push(err.message));

  await offlinePage.goto(`${origin}${probePage}`, { waitUntil: 'domcontentloaded' });
  const isControlled = await offlinePage.evaluate(() => Boolean(navigator.serviceWorker.controller));
  check('offline page is controlled by upgraded worker', isControlled);

  const testResults = await offlinePage.evaluate(async () => {
    const res = {};

    // 1. Domain modules
    const htmlMod = await import('/static/js/shared/domain/html.js');
    res.escaped = htmlMod.escapeHtml('<span class="test">');

    const relTimeMod = await import('/static/js/shared/domain/relativeTime.js');
    res.archiveAge = relTimeMod.formatArchiveAge(new Date(Date.now() - 120000).toISOString(), Date.now());

    const urlMod = await import('/static/js/shared/domain/urlSchemes.js');
    res.compactedScheme = urlMod.compactUrlSchemeValue('  HTTPS://EXAMPLE.COM  ');

    // 2. Platform modules
    const clipMod = await import('/static/js/shared/platform/clipboard.js');
    res.hasClipFunc = typeof clipMod.withClipboardTextarea === 'function';

    const debMod = await import('/static/js/shared/platform/debounce.js');
    res.hasDebounceFunc = typeof debMod.debounceTrailing === 'function';

    const downMod = await import('/static/js/shared/platform/downloads.js');
    res.hasDownloadFunc = typeof downMod.downloadBlob === 'function';

    // 3. UI modules
    const optMod = await import('/static/js/shared/ui/optionNavigation.js');
    res.steppedOption = optMod.stepWrappedOption(0, 5, 1);

    const escMod = await import('/static/js/escMenuStack.js');
    res.hasEscStack = typeof escMod.registerEscapeLayer === 'function';

    const dragMod = await import('/static/js/windowDrag.js');
    res.hasDrag = typeof dragMod.makeWindowDraggable === 'function';

    const resizeMod = await import('/static/js/windowResize.js');
    res.hasResize = typeof resizeMod.makeWindowResizable === 'function';

    const snapMod = await import('/static/js/modalSnap.js');
    res.hasSnap = typeof snapMod.makeRightDockController === 'function';

    // 4. Updated precached entry points and their new dependencies
    const galleryEditor = await import('/static/js/galleryEditor.js?v=20260909movepicklayer1');
    res.galleryEditorImported = typeof galleryEditor.openEditor === 'function' || Boolean(galleryEditor);

    const panels = await import('/static/js/panels.js?v=20260909movepicklayer1');
    res.panelsImported = typeof panels.panelNames === 'function';

    const settings = await import('/static/js/settings.js?v=20260912writingstyle3');
    res.settingsImported = Boolean(settings.default);

    const agentForm = await import('/static/js/settings/integrations/forms/agent.js');
    res.agentFormImported = Boolean(agentForm);

    const carddavForm = await import('/static/js/settings/integrations/forms/carddav.js');
    res.carddavFormImported = Boolean(carddavForm);

    const emailForm = await import('/static/js/settings/integrations/forms/email.js');
    res.emailFormImported = Boolean(emailForm);

    const settingsSearch = await import('/static/js/settings/search.js');
    res.settingsSearchImported = Boolean(settingsSearch);

    const preflight = await import('/static/js/editor/preflight.js');
    res.preflightImported = Boolean(preflight);

    const cookbookDirs = await import('/static/js/cookbookModelDirectories.js');
    res.normalizedDir = cookbookDirs.normalizeCookbookModelDir('home/user/models');

    const cookbookSchedule = await import('/static/js/cookbookSchedule.js');
    res.cookbookScheduleImported = Boolean(cookbookSchedule);

    const authStatus = await import('/static/js/authStatus.js');
    res.authStatusImported = Boolean(authStatus);

    const appConfig = await import('/static/js/appConfig.js');
    res.appConfigImported = Boolean(appConfig);

    const storage = await import('/static/js/storage.js');
    res.storageImported = Boolean(storage);

    const confetti = await import('/static/js/shared/ui/confetti.js');
    res.confettiImported = Boolean(confetti);

    // Track resources loaded by the offline page
    res.loadedUrls = performance.getEntriesByType('resource').map(e => new URL(e.name).pathname);

    return res;
  });

  check('domain modules loaded offline correctly',
    testResults.escaped === '&lt;span class=&quot;test&quot;&gt;' &&
    testResults.archiveAge === '2m ago' &&
    testResults.compactedScheme === 'https://example.com',
    JSON.stringify({ escaped: testResults.escaped, archiveAge: testResults.archiveAge, compactedScheme: testResults.compactedScheme })
  );

  check('platform modules loaded offline correctly',
    testResults.hasClipFunc && testResults.hasDebounceFunc && testResults.hasDownloadFunc
  );

  check('UI modules loaded offline correctly',
    testResults.steppedOption === 1 && testResults.hasEscStack && testResults.hasDrag && testResults.hasResize && testResults.hasSnap
  );

  check('updated consumer entry points loaded offline with new dependencies',
    testResults.galleryEditorImported &&
    testResults.panelsImported &&
    testResults.settingsImported &&
    testResults.agentFormImported &&
    testResults.carddavFormImported &&
    testResults.emailFormImported &&
    testResults.settingsSearchImported &&
    testResults.preflightImported &&
    testResults.normalizedDir === '/home/user/models' &&
    testResults.cookbookScheduleImported &&
    testResults.authStatusImported &&
    testResults.appConfigImported &&
    testResults.storageImported &&
    testResults.confettiImported
  );

  const unexpectedFailures = failedRequests.filter(url => !url.includes('/api/'));
  check('zero unexpected failed requests offline', unexpectedFailures.length === 0, unexpectedFailures.join(', '));
  const MODULE_LOAD_FAILURE = /Failed to fetch dynamically imported module|Failed to load module script|Failed to resolve module specifier|error loading dynamically imported module/i;
  const moduleErrors = [...consoleErrors, ...pageErrors].filter(text => MODULE_LOAD_FAILURE.test(text));
  check('zero module load errors offline', moduleErrors.length === 0, moduleErrors.join(' | '));

  console.log(`\nUpgrade and offline transition verification completed successfully!`);
}

main()
  .catch(err => {
    console.error(err);
    check('upgrade script ran to completion', false, err.message);
  })
  .finally(async () => {
    if (context) await context.close().catch(() => {});
    const failed = results.filter(r => !r.pass);
    console.log(`\n${results.length - failed.length}/${results.length} checks passed`);
    if (failed.length) {
      for (const s of servers) console.log(`--- ${s.name} log tail ---\n${s.log().slice(-1000)}`);
      process.exitCode = 1;
    }
    cleanup();
  });
