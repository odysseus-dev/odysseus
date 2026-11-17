import { spawn, spawnSync, execSync } from 'child_process';
import { chromium } from 'playwright';
import { existsSync, unlinkSync, mkdirSync, mkdtempSync, rmSync, readFileSync } from 'fs';
import { resolve, join } from 'path';
import { tmpdir, homedir } from 'os';
import { fileURLToPath } from 'url';
import net from 'net';

const REPO_ROOT = resolve(process.env.REPO_ROOT || '.');
const isPosix = process.platform !== 'win32';
let TEST_TEMP_DIR = null;
let DATA_DIR = null;
let DB_PATH = null;
let FASTEMBED_DIR = null;
let MAIL_DIR = null;
let RUNTIME_DIR = null;
const ARTIFACT_DIR =
  process.env.ARTIFACT_DIR ||
  process.env.NOTES_NATIVE_EVIDENCE_DIR ||
  join(tmpdir(), 'odysseus-fallback-recovery-evidence');

export function resolveAppPort(rawPort = process.env.PORT) {
  if (rawPort === undefined || rawPort === null || rawPort === '') {
    return null;
  }
  const parsed = Number(rawPort);
  if (!Number.isInteger(parsed) || parsed < 1 || parsed > 65535) {
    throw new Error(`Invalid PORT configured: "${rawPort}". Must be an integer between 1 and 65535.`);
  }
  if (parsed === 8100) {
    throw new Error('Configured PORT cannot be 8100 (reserved for persistent ChromaDB guard isolation).');
  }
  return parsed;
}

export async function getEphemeralPort(excludePorts = new Set(), maxAttempts = 10) {
  const excluded = excludePorts instanceof Set ? excludePorts : new Set(excludePorts || []);
  for (let attempt = 1; attempt <= maxAttempts; attempt++) {
    const port = await new Promise((resolvePort, rejectPort) => {
      const srv = net.createServer();
      srv.unref();
      srv.on('error', rejectPort);
      srv.listen(0, '127.0.0.1', () => {
        const addr = srv.address();
        const p = typeof addr === 'object' && addr ? addr.port : null;
        srv.close((err) => {
          if (err) rejectPort(err);
          else resolvePort(p);
        });
      });
    });
    if (port && !excluded.has(port)) {
      return port;
    }
  }
  throw new Error(`Failed to allocate an ephemeral port not in [${Array.from(excluded).join(', ')}] after ${maxAttempts} attempts.`);
}

export async function fetchChromaToolCount(host = '127.0.0.1', port = 8100, timeoutMs = 1000) {
  try {
    const hbRes = await fetch(`http://${host}:${port}/api/v2/heartbeat`, { signal: AbortSignal.timeout(timeoutMs) });
    if (!hbRes.ok) return null;
    const colRes = await fetch(`http://${host}:${port}/api/v2/tenants/default_tenant/databases/default_database/collections`, { signal: AbortSignal.timeout(timeoutMs) });
    if (!colRes.ok) return null;
    const cols = await colRes.json();
    const toolCol = cols.find((c) => c.name && c.name.includes('tool_index'));
    if (!toolCol) return 0;
    const countRes = await fetch(`http://${host}:${port}/api/v2/tenants/default_tenant/databases/default_database/collections/${toolCol.id}/count`, { signal: AbortSignal.timeout(timeoutMs) });
    if (!countRes.ok) return null;
    return await countRes.json();
  } catch (_) {
    return null;
  }
}

function resolvePython() {
  const candidates = [
    '/usr/bin/python3',
    process.env.PYTHON,
    process.env.ODYSSEUS_TEST_PYTHON,
    process.env.VIRTUAL_ENV ? join(process.env.VIRTUAL_ENV, 'bin', 'python') : null,
    join(REPO_ROOT, 'venv', 'bin', 'python'),
    join(REPO_ROOT, '.venv', 'bin', 'python'),
    'python3',
    'python',
  ].filter(Boolean);

  for (const cand of candidates) {
    try {
      const res = spawnSync(cand, ['-c', 'import uvicorn'], { stdio: 'ignore' });
      if (res.status === 0) return cand;
    } catch (_) {}
  }
  return '/usr/bin/python3';
}

function initTestEnvironment() {
  TEST_TEMP_DIR = mkdtempSync(join(tmpdir(), 'odysseus-fallback-test-'));
  DATA_DIR = join(TEST_TEMP_DIR, 'data');
  mkdirSync(DATA_DIR, { recursive: true });
  DB_PATH = join(TEST_TEMP_DIR, 'test.db');
  FASTEMBED_DIR = join(TEST_TEMP_DIR, 'fastembed');
  mkdirSync(FASTEMBED_DIR, { recursive: true });
  MAIL_DIR = join(TEST_TEMP_DIR, 'mail');
  mkdirSync(MAIL_DIR, { recursive: true });
  RUNTIME_DIR = join(TEST_TEMP_DIR, 'runtime');
  mkdirSync(RUNTIME_DIR, { recursive: true });

  if (existsSync(DB_PATH)) {
    try { unlinkSync(DB_PATH); } catch (_) {}
  }

  const initSql = `
    CREATE TABLE IF NOT EXISTS model_endpoints (
      id TEXT PRIMARY KEY,
      name TEXT NOT NULL,
      base_url TEXT NOT NULL,
      api_key TEXT,
      is_enabled INTEGER DEFAULT 1,
      hidden_models TEXT,
      cached_models TEXT,
      pinned_models TEXT,
      cookbook_serve_session_id TEXT,
      model_type TEXT DEFAULT 'llm',
      endpoint_kind TEXT DEFAULT 'auto',
      model_refresh_mode TEXT DEFAULT 'manual',
      model_refresh_interval INTEGER,
      model_refresh_timeout INTEGER,
      supports_tools INTEGER,
      model_tool_modes TEXT,
      owner TEXT,
      provider_auth_id TEXT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    INSERT INTO model_endpoints (id, name, base_url, model_type, is_enabled, cached_models)
    VALUES ('ep-ollama-local', 'Ollama Local (Qwen)', 'http://127.0.0.1:11434/v1', 'llm', 1, '["qwen2.5:7b", "llama3.1:8b"]');

    INSERT INTO model_endpoints (id, name, base_url, model_type, is_enabled, cached_models)
    VALUES ('ep-remote-fast', 'Remote Fast (Claude)', 'http://127.0.0.1:8080/v1', 'llm', 1, '["claude-3-5-haiku", "gpt-4o-mini"]');
  `;

  execSync(`sqlite3 "${DB_PATH}"`, { input: initSql });
  console.log(`Initialized test database at ${DB_PATH}`);
}

let serverProc = null;
let browserInstance = null;

function cleanup() {
  if (browserInstance) {
    try { browserInstance.close(); } catch (_) {}
    browserInstance = null;
  }
  if (serverProc) {
    const pid = serverProc.pid;
    try {
      if (isPosix && pid) {
        try { process.kill(-pid, 'SIGTERM'); } catch (_) {}
        try { process.kill(-pid, 'SIGKILL'); } catch (_) {}
      } else {
        serverProc.kill('SIGKILL');
      }
    } catch (_) {}
    serverProc = null;
  }
  if (TEST_TEMP_DIR && existsSync(TEST_TEMP_DIR)) {
    try { rmSync(TEST_TEMP_DIR, { recursive: true, force: true }); } catch (_) {}
  }
}

process.on('exit', cleanup);
process.on('SIGINT', () => { cleanup(); process.exit(130); });
process.on('SIGTERM', () => { cleanup(); process.exit(143); });
process.on('uncaughtException', (err) => { console.error(err); cleanup(); process.exit(1); });
process.on('unhandledRejection', (err) => { console.error(err); cleanup(); process.exit(1); });

async function waitForServer(url, timeoutMs = 25000) {
  const start = Date.now();
  while (Date.now() - start < timeoutMs) {
    try {
      const res = await fetch(url);
      if (res.ok || res.status === 404 || res.status === 200) return true;
    } catch (_) {}
    await new Promise((r) => setTimeout(r, 400));
  }
  throw new Error(`Server failed to start within ${timeoutMs}ms at ${url}`);
}

export async function runBrowserTests() {
  initTestEnvironment();
  const explicitPort = resolveAppPort();
  const port = explicitPort ?? (await getEphemeralPort(new Set([8100])));
  const chromaGuardPort = await getEphemeralPort(new Set([8100, port]));

  // Preflight isolation check
  const preChromaCount = await fetchChromaToolCount('127.0.0.1', 8100);
  if (preChromaCount !== null) {
    console.log(`[isolation-check] Live ChromaDB detected at 8100; tool_index item count = ${preChromaCount}`);
  }

  const pythonBin = resolvePython();
  console.log(`Starting isolated Odysseus test instance on port ${port} (Chroma guard port ${chromaGuardPort}) using ${pythonBin}...`);

  const serverEnv = {
    ...process.env,
    ODYSSEUS_DATA_DIR: DATA_DIR,
    DATABASE_URL: `sqlite:///${DB_PATH}`,
    AUTH_ENABLED: 'false',
    PORT: String(port),
    ODYSSEUS_TOOL_INDEX_PREWARM: '0',
    ODYSSEUS_STARTUP_WARMUPS: '0',
    ODYSSEUS_MODEL_KEEPALIVE: '0',
    CHROMADB_HOST: '127.0.0.1',
    CHROMADB_PORT: String(chromaGuardPort),
    FASTEMBED_CACHE_PATH: FASTEMBED_DIR,
    ODYSSEUS_MAIL_ATTACHMENTS_DIR: MAIL_DIR,
    XDG_RUNTIME_DIR: RUNTIME_DIR,
    PYTHONPATH: REPO_ROOT,
    OPENAI_API_KEY: '',
    ANTHROPIC_API_KEY: '',
    XAI_API_KEY: '',
    MISTRAL_API_KEY: '',
    GROQ_API_KEY: '',
    GEMINI_API_KEY: '',
    DEEPSEEK_API_KEY: '',
    COHERE_API_KEY: '',
    SEARXNG_URL: '',
    NTFY_URL: '',
    OLLAMA_BASE_URL: 'http://127.0.0.1:0',
    OLLAMA_URL: 'http://127.0.0.1:0',
  };

  serverProc = spawn(
    pythonBin,
    ['-m', 'uvicorn', 'app:app', '--host', '127.0.0.1', '--port', String(port), '--log-level', 'warning'],
    {
      env: serverEnv,
      cwd: REPO_ROOT,
      detached: isPosix,
      stdio: ['ignore', 'pipe', 'pipe'],
    }
  );

  serverProc.stdout.on('data', (d) => {
    const s = d.toString().trim();
    if (s) console.log(`[server] ${s}`);
  });
  serverProc.stderr.on('data', (d) => {
    const s = d.toString().trim();
    if (s && !s.includes('VectorRAG init failed') && !s.includes('MemoryVectorStore init failed')) {
      console.log(`[server err] ${s}`);
    }
  });

  const baseUrl = `http://127.0.0.1:${port}`;
  await waitForServer(`${baseUrl}/api/auth/status`, 25000);
  console.log(`Server is healthy at ${baseUrl}. Launching Playwright browser...`);

  mkdirSync(ARTIFACT_DIR, { recursive: true });

  browserInstance = await chromium.launch({
    headless: true,
    args: ['--no-sandbox', '--disable-dev-shm-usage'],
  });

  const context = await browserInstance.newContext({
    viewport: { width: 1440, height: 900 },
  });

  await context.tracing.start({ screenshots: true, snapshots: true });

  const page = await context.newPage();
  page.on('pageerror', (err) => console.error('[browser page error]', err));

  const testReport = {
    testA: {
      status: 'PENDING',
      evidence: [],
      intercepted: [],
    },
    testB: {
      status: 'PENDING',
      evidence: [],
      intercepted: [],
    },
    persistence: {},
    screenshots: {},
  };

  try {
    console.log('\n======================================================');
    console.log('BEGIN TEST A: Failed preference write followed by Retry');
    console.log('======================================================\n');

    await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(600);

    // 1. Open Settings -> AI
    console.log('Step 1: Opening Settings -> AI...');
    await page.evaluate(() => {
      if (window.adminModule && window.adminModule.open) {
        window.adminModule.open('ai');
      } else if (window.settingsModule && window.settingsModule.open) {
        window.settingsModule.open('ai');
      }
    });

    await page.waitForSelector('#set-foregroundFallbackToggle', { state: 'attached', timeout: 10000 });
    // Wait until preferences load and toggle is enabled
    await page.waitForFunction(() => {
      const toggle = document.getElementById('set-foregroundFallbackToggle');
      return toggle && !toggle.disabled;
    }, undefined, { timeout: 10000 });

    const toggle = page.locator('#set-foregroundFallbackToggle');
    const toggleLabel = page.locator('label:has(#set-foregroundFallbackToggle)');
    const editor = page.locator('#set-foregroundFallbackEditor');
    const state = page.locator('#set-foregroundFallbackState');
    const retry = page.locator('#set-foregroundFallbackRetry');
    const addBtn = page.locator('#set-foregroundAddFallback');

    const initialChecked = await toggle.isChecked();
    const initialHidden = await editor.getAttribute('hidden');
    const initialRetryHidden = await retry.getAttribute('hidden');
    console.log(`Initial state: checked=${initialChecked}, editorHidden=${initialHidden !== null}, retryHidden=${initialRetryHidden !== null}`);

    if (initialChecked) throw new Error('Expected foreground fallback toggle to be off by default');
    if (initialHidden === null) throw new Error('Expected editor to be hidden when toggle is off');

    // 2. Enable foreground fallback
    console.log('Step 2: Enabling foreground fallback...');
    const enablePutPromise = page.waitForResponse(
      (res) => res.url().includes('/api/prefs/foreground_fallback_enabled') && res.request().method() === 'PUT'
    );
    await toggleLabel.click();
    const enablePutRes = await enablePutPromise;
    console.log(`Enable PUT response: ${enablePutRes.status()}`);
    if (enablePutRes.status() !== 200) throw new Error(`Enable PUT failed with status ${enablePutRes.status()}`);

    await page.waitForFunction(() => {
      const ed = document.getElementById('set-foregroundFallbackEditor');
      return ed && !ed.hidden;
    }, undefined, { timeout: 5000 });

    const enabledChecked = await toggle.isChecked();
    console.log(`Toggle checked=${enabledChecked}, editor hidden=${await editor.getAttribute('hidden')}`);
    if (!enabledChecked) throw new Error('Toggle should be checked after click');

    // 3. Configure two eligible fallback candidates
    console.log('Step 3: Configuring two eligible fallback candidates...');
    // Add first candidate
    const add1Promise = page.waitForResponse(
      (res) => res.url().includes('/api/prefs/foreground_model_fallbacks') && res.request().method() === 'PUT'
    );
    await addBtn.click();
    const add1Res = await add1Promise;
    console.log(`Add candidate 1 PUT response: ${add1Res.status()}`);
    if (add1Res.status() !== 200) throw new Error(`Candidate 1 PUT failed with status ${add1Res.status()}`);

    await page.waitForFunction(() => {
      return document.querySelectorAll('#set-foregroundFallbacks .settings-fallback-row').length === 1;
    }, undefined, { timeout: 5000 });

    // Add second candidate
    const add2Promise = page.waitForResponse(
      (res) => res.url().includes('/api/prefs/foreground_model_fallbacks') && res.request().method() === 'PUT'
    );
    await addBtn.click();
    const add2Res = await add2Promise;
    console.log(`Add candidate 2 PUT response: ${add2Res.status()}`);
    if (add2Res.status() !== 200) throw new Error(`Candidate 2 PUT failed with status ${add2Res.status()}`);

    await page.waitForFunction(() => {
      return document.querySelectorAll('#set-foregroundFallbacks .settings-fallback-row').length === 2;
    }, undefined, { timeout: 5000 });

    const candidateRows = page.locator('#set-foregroundFallbacks .settings-fallback-row');
    const rowCount = await candidateRows.count();
    console.log(`Candidate rows count: ${rowCount}`);
    if (rowCount !== 2) throw new Error(`Expected 2 candidate rows, got ${rowCount}`);

    const candidate1Ep = await candidateRows.nth(0).locator('select').nth(0).inputValue();
    const candidate1Model = await candidateRows.nth(0).locator('select').nth(1).inputValue();
    const candidate2Ep = await candidateRows.nth(1).locator('select').nth(0).inputValue();
    const candidate2Model = await candidateRows.nth(1).locator('select').nth(1).inputValue();
    console.log(`Configured candidates: 1: [${candidate1Ep}, ${candidate1Model}], 2: [${candidate2Ep}, ${candidate2Model}]`);

    const screenshot1Path = join(ARTIFACT_DIR, 'desktop-01-settings-ai-enabled-initial.png');
    await page.screenshot({ path: screenshot1Path });
    testReport.screenshots.step3_configured = screenshot1Path;
    console.log(`Saved screenshot 1 to ${screenshot1Path}`);

    // 4. Intercept the next candidate-list preference PUT and return HTTP 500
    console.log('Step 4: Arming PUT route interception to return HTTP 500 on next candidate edit...');
    let interceptPut500Active = true;
    let interceptedPutA = null;

    await page.route('**/api/prefs/foreground_model_fallbacks', async (route) => {
      const req = route.request();
      if (req.method() === 'PUT' && interceptPut500Active) {
        interceptPut500Active = false;
        interceptedPutA = {
          url: req.url(),
          method: req.method(),
          headers: req.headers(),
          postData: req.postData(),
        };
        console.log(`[INTERCEPTOR] Intercepted PUT to ${req.url()} with body: ${req.postData()}`);
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Simulated internal server error during preference write' }),
        });
      } else {
        await route.continue();
      }
    });

    // Trigger candidate edit via Move Down on Row 1
    console.log('Triggering candidate reorder (Move Down on candidate 1)...');
    const moveDownBtnRow1 = candidateRows.nth(0).locator('.settings-fallback-move[data-move-offset="1"]');
    await moveDownBtnRow1.click();

    // Wait for the failure to be processed by Settings controller
    await page.waitForFunction(() => {
      const retryBtn = document.getElementById('set-foregroundFallbackRetry');
      return retryBtn && !retryBtn.hidden;
    }, undefined, { timeout: 6000 });

    if (!interceptedPutA) throw new Error('Expected candidate list PUT to be intercepted');
    testReport.testA.intercepted.push({
      request: interceptedPutA,
      responseStatus: 500,
    });
    console.log('Successfully intercepted PUT with HTTP 500 response.');

    // 5. Verify that the editor becomes disabled
    console.log('Step 5: Verifying editor disabled state...');
    const editorHiddenAfterFailure = await editor.getAttribute('hidden');
    const toggleDisabledAfterFailure = await toggle.isDisabled();
    const stateTextAfterFailure = (await state.textContent()).trim();
    console.log(`Failure state: editorHidden=${editorHiddenAfterFailure !== null}, toggleDisabled=${toggleDisabledAfterFailure}, stateText="${stateTextAfterFailure}"`);

    if (editorHiddenAfterFailure === null) throw new Error('Expected editor to be hidden when preferences become unavailable');
    if (!toggleDisabledAfterFailure) throw new Error('Expected toggle to be disabled');
    if (!stateTextAfterFailure.includes('Fallback preferences are unavailable. Retry before changing this policy.')) {
      throw new Error(`Unexpected state text: "${stateTextAfterFailure}"`);
    }

    // Also check widget controls inside the container
    const addDisabledAfterFailure = await addBtn.isDisabled();
    if (!addDisabledAfterFailure) throw new Error('Add fallback button should be disabled');

    const selectsDisabled = await page.evaluate(() => {
      const selects = document.querySelectorAll('#set-foregroundFallbacks select');
      return Array.from(selects).every((s) => s.disabled);
    });
    console.log(`All fallback row selects disabled: ${selectsDisabled}`);
    if (!selectsDisabled) throw new Error('Expected all fallback row selects to be disabled');

    // 6. Verify Retry becomes available
    console.log('Step 6: Verifying Retry button is visible and enabled...');
    const retryHiddenAfterFailure = await retry.getAttribute('hidden');
    const retryDisabledAfterFailure = await retry.isDisabled();
    console.log(`Retry button: hidden=${retryHiddenAfterFailure !== null}, disabled=${retryDisabledAfterFailure}`);
    if (retryHiddenAfterFailure !== null) throw new Error('Expected Retry button to be visible');
    if (retryDisabledAfterFailure) throw new Error('Expected Retry button to be enabled');

    const screenshot2Path = join(ARTIFACT_DIR, 'desktop-02-settings-ai-failure-retry.png');
    await page.screenshot({ path: screenshot2Path });
    testReport.screenshots.step6_failure = screenshot2Path;
    console.log(`Saved screenshot 2 to ${screenshot2Path}`);

    // 7. Restore normal network connectivity
    console.log('Step 7: Restoring normal network connectivity...');
    // interceptPut500Active is already false, so subsequent PUTs continue normally

    // 8 & 9. Click Retry. Verify preferences reload successfully without refetching model catalog.
    console.log('Step 8 & 9: Clicking Retry and monitoring network requests...');
    const retryNetworkRequests = [];
    const requestListener = (req) => {
      retryNetworkRequests.push({ method: req.method(), url: req.url() });
    };
    page.on('request', requestListener);

    await retry.click();

    // Wait for Retry to complete: retry button becomes hidden
    await page.waitForFunction(() => {
      const retryBtn = document.getElementById('set-foregroundFallbackRetry');
      return retryBtn && retryBtn.hidden;
    }, undefined, { timeout: 10000 });

    page.off('request', requestListener);

    console.log(`Network requests during Retry: ${JSON.stringify(retryNetworkRequests)}`);
    const prefsReloadReq = retryNetworkRequests.find((r) => r.url.endsWith('/api/prefs') && r.method === 'GET');
    const catalogRefetchReq = retryNetworkRequests.find((r) => r.url.includes('/api/models?background=false&foreground_fallback=true'));

    if (!prefsReloadReq) throw new Error('Expected GET /api/prefs to be called during Retry');
    if (catalogRefetchReq) throw new Error('Catalog was unnecessarily refetched during preference retry!');
    console.log('Verified: Preferences reloaded via GET /api/prefs without refetching the already-loaded model catalog.');

    // 10. Verify editor, Add button, candidate selectors, and eligible reorder/remove controls become enabled
    console.log('Step 10: Verifying re-enabled controls...');
    const editorHiddenAfterRetry = await editor.getAttribute('hidden');
    const toggleDisabledAfterRetry = await toggle.isDisabled();
    const addDisabledAfterRetry = await addBtn.isDisabled();
    console.log(`After Retry: editorHidden=${editorHiddenAfterRetry !== null}, toggleDisabled=${toggleDisabledAfterRetry}, addDisabled=${addDisabledAfterRetry}`);

    if (editorHiddenAfterRetry !== null) throw new Error('Expected editor to be visible after successful Retry');
    if (toggleDisabledAfterRetry) throw new Error('Expected toggle to be enabled after Retry');
    if (addDisabledAfterRetry) throw new Error('Expected Add button to be enabled after Retry');

    const rowsAfterRetry = page.locator('#set-foregroundFallbacks .settings-fallback-row');
    const rowsCountAfterRetry = await rowsAfterRetry.count();
    if (rowsCountAfterRetry !== 2) throw new Error(`Expected 2 rows after retry, got ${rowsCountAfterRetry}`);

    // Verify row 1 controls
    const row1EpDisabled = await rowsAfterRetry.nth(0).locator('select').nth(0).isDisabled();
    const row1ModelDisabled = await rowsAfterRetry.nth(0).locator('select').nth(1).isDisabled();
    const row1MoveUpDisabled = await rowsAfterRetry.nth(0).locator('.settings-fallback-move[data-move-offset="-1"]').isDisabled();
    const row1MoveDownDisabled = await rowsAfterRetry.nth(0).locator('.settings-fallback-move[data-move-offset="1"]').isDisabled();
    const row1RemoveDisabled = await rowsAfterRetry.nth(0).locator('.settings-fallback-remove:not(.settings-fallback-move)').isDisabled();

    // Verify row 2 controls
    const row2EpDisabled = await rowsAfterRetry.nth(1).locator('select').nth(0).isDisabled();
    const row2ModelDisabled = await rowsAfterRetry.nth(1).locator('select').nth(1).isDisabled();
    const row2MoveUpDisabled = await rowsAfterRetry.nth(1).locator('.settings-fallback-move[data-move-offset="-1"]').isDisabled();
    const row2MoveDownDisabled = await rowsAfterRetry.nth(1).locator('.settings-fallback-move[data-move-offset="1"]').isDisabled();
    const row2RemoveDisabled = await rowsAfterRetry.nth(1).locator('.settings-fallback-remove:not(.settings-fallback-move)').isDisabled();

    console.log(`Row 1: epDisabled=${row1EpDisabled}, modelDisabled=${row1ModelDisabled}, moveUpDisabled=${row1MoveUpDisabled} (expected true), moveDownDisabled=${row1MoveDownDisabled} (expected false), removeDisabled=${row1RemoveDisabled}`);
    console.log(`Row 2: epDisabled=${row2EpDisabled}, modelDisabled=${row2ModelDisabled}, moveUpDisabled=${row2MoveUpDisabled} (expected false), moveDownDisabled=${row2MoveDownDisabled} (expected true), removeDisabled=${row2RemoveDisabled}`);

    if (row1EpDisabled || row1ModelDisabled || row1RemoveDisabled) throw new Error('Row 1 controls should be enabled');
    if (!row1MoveUpDisabled) throw new Error('Row 1 move up should be disabled (boundary: first row)');
    if (row1MoveDownDisabled) throw new Error('Row 1 move down should be enabled');

    if (row2EpDisabled || row2ModelDisabled || row2RemoveDisabled) throw new Error('Row 2 controls should be enabled');
    if (row2MoveUpDisabled) throw new Error('Row 2 move up should be enabled');
    if (!row2MoveDownDisabled) throw new Error('Row 2 move down should be disabled (boundary: last row)');

    const screenshot3Path = join(ARTIFACT_DIR, 'desktop-03-settings-ai-recovered.png');
    await page.screenshot({ path: screenshot3Path });
    testReport.screenshots.step10_recovered = screenshot3Path;
    console.log(`Saved screenshot 3 to ${screenshot3Path}`);

    // 11. Perform another candidate edit
    console.log('Step 11: Performing another candidate edit (Row 1 Move Down)...');
    const moveDownPromise = page.waitForResponse(
      (res) => res.url().includes('/api/prefs/foreground_model_fallbacks') && res.request().method() === 'PUT'
    );
    await rowsAfterRetry.nth(0).locator('.settings-fallback-move[data-move-offset="1"]').click();

    // 12. Verify subsequent PUT succeeds
    console.log('Step 12: Awaiting subsequent PUT response...');
    const moveDownRes = await moveDownPromise;
    console.log(`Subsequent PUT response status: ${moveDownRes.status()}`);
    if (moveDownRes.status() !== 200) throw new Error(`Subsequent PUT failed with status ${moveDownRes.status()}`);

    // 13. Reload preferences and verify final candidate list and order were persisted
    console.log('Step 13: Verifying persistence in backend preferences...');
    const verifyPrefsRes = await fetch(`${baseUrl}/api/prefs`);
    const verifyPrefsData = await verifyPrefsRes.json();
    console.log(`Persisted prefs: ${JSON.stringify(verifyPrefsData)}`);

    if (!verifyPrefsData.foreground_fallback_enabled) {
      throw new Error('Expected foreground_fallback_enabled to be true in backend prefs');
    }
    const persistedFallbacks = verifyPrefsData.foreground_model_fallbacks;
    if (!Array.isArray(persistedFallbacks) || persistedFallbacks.length !== 2) {
      throw new Error(`Expected 2 persisted fallbacks, got ${JSON.stringify(persistedFallbacks)}`);
    }

    // Verify order was flipped: candidate 2 is now first, candidate 1 is now second
    if (
      persistedFallbacks[0].endpoint_id !== candidate2Ep ||
      persistedFallbacks[0].model !== candidate2Model ||
      persistedFallbacks[1].endpoint_id !== candidate1Ep ||
      persistedFallbacks[1].model !== candidate1Model
    ) {
      throw new Error(`Persisted order mismatch. Expected [${candidate2Ep}:${candidate2Model}, ${candidate1Ep}:${candidate1Model}], got ${JSON.stringify(persistedFallbacks)}`);
    }

    console.log('Verified: Candidate reordering persisted successfully in backend storage.');
    testReport.testA.status = 'PASS';
    testReport.testA.evidence.push('Toggle enabled -> 2 candidates added -> simulated PUT 500 error -> editor disabled & Retry visible -> Retry restored without catalog fetch -> controls re-enabled -> candidate reorder persisted.');

    console.log('\n======================================================');
    console.log('BEGIN TEST B: Catalog refresh while preferences unavailable');
    console.log('======================================================\n');

    // 1. Trigger another preference-write failure
    console.log('Step B1: Arming PUT route interception to fail preference write...');
    let interceptPutBActive = true;
    let interceptedPutB = null;

    await page.route('**/api/prefs/foreground_model_fallbacks', async (route) => {
      const req = route.request();
      if (req.method() === 'PUT' && interceptPutBActive) {
        interceptPutBActive = false;
        interceptedPutB = {
          url: req.url(),
          method: req.method(),
          headers: req.headers(),
          postData: req.postData(),
        };
        console.log(`[INTERCEPTOR] Intercepted PUT for Test B to ${req.url()}`);
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Simulated failure for Test B' }),
        });
      } else {
        await route.continue();
      }
    });

    console.log('Triggering candidate move up on row 2...');
    const row2MoveUp = page.locator('#set-foregroundFallbacks .settings-fallback-row').nth(1).locator('.settings-fallback-move[data-move-offset="-1"]');
    await row2MoveUp.click();

    await page.waitForFunction(() => {
      const retryBtn = document.getElementById('set-foregroundFallbackRetry');
      return retryBtn && !retryBtn.hidden;
    }, undefined, { timeout: 6000 });

    if (!interceptedPutB) throw new Error('Expected candidate list PUT to be intercepted for Test B');
    testReport.testB.intercepted.push({
      request: interceptedPutB,
      responseStatus: 500,
    });
    console.log('Preference write failed as expected; editor disabled and Retry button active.');

    // 2. Keep preferences unavailable
    console.log('Step B2: Keeping preferences unavailable by intercepting GET /api/prefs with HTTP 500...');
    let prefsUnavailableB = true;
    await page.route('**/api/prefs', async (route) => {
      if (prefsUnavailableB) {
        console.log('[INTERCEPTOR] Returning 500 for GET /api/prefs');
        await route.fulfill({
          status: 500,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Preferences backend offline' }),
        });
      } else {
        await route.continue();
      }
    });

    // 3. Trigger a successful foreground model-catalog refresh
    console.log('Step B3: Triggering a successful foreground model-catalog refresh...');
    const catalogRefreshPromise = page.waitForResponse(
      (res) => res.url().includes('/api/models?background=false&foreground_fallback=true') && res.status() === 200
    );

    await page.evaluate(async () => {
      const m = await import('/static/js/settings.js?v=20260912writingstyle3');
      await m.refreshAiModelEndpoints();
    });

    const catalogRefreshRes = await catalogRefreshPromise;
    console.log(`Model catalog refresh response status: ${catalogRefreshRes.status()}`);
    if (catalogRefreshRes.status() !== 200) throw new Error('Catalog refresh failed');

    // 4. Verify that the editor remains disabled
    console.log('Step B4: Verifying that editor remains disabled despite catalog refresh...');
    await page.waitForTimeout(400);

    const bEditorHidden = await editor.getAttribute('hidden');
    const bToggleDisabled = await toggle.isDisabled();
    const bRetryHidden = await retry.getAttribute('hidden');
    const bStateText = (await state.textContent()).trim();
    console.log(`After catalog refresh: editorHidden=${bEditorHidden !== null}, toggleDisabled=${bToggleDisabled}, retryHidden=${bRetryHidden !== null}`);
    console.log(`State text: "${bStateText}"`);

    if (bEditorHidden === null) throw new Error('Editor should remain hidden while preferences are unavailable');
    if (!bToggleDisabled) throw new Error('Toggle should remain disabled');
    if (bRetryHidden !== null) throw new Error('Retry button should remain visible');
    if (!bStateText.includes('Fallback preferences are unavailable. Retry before changing this policy.')) {
      throw new Error(`Unexpected state text during preference unavailability: "${bStateText}"`);
    }

    const bSelectsDisabled = await page.evaluate(() => {
      const selects = document.querySelectorAll('#set-foregroundFallbacks select');
      return Array.from(selects).every((s) => s.disabled);
    });
    if (!bSelectsDisabled) throw new Error('Fallback row selects must remain disabled while preferences unavailable');

    const screenshot4Path = join(ARTIFACT_DIR, 'desktop-04-settings-ai-test-b-catalog-refreshed.png');
    await page.screenshot({ path: screenshot4Path });
    testReport.screenshots.stepB4_catalog_refreshed = screenshot4Path;
    console.log(`Saved screenshot 4 to ${screenshot4Path}`);

    // 5. Recover preferences through Retry
    console.log('Step B5: Restoring preference availability and clicking Retry...');
    prefsUnavailableB = false;

    const retryBReqs = [];
    const recordReqB = (req) => retryBReqs.push({ method: req.method(), url: req.url() });
    page.on('request', recordReqB);

    await retry.click();

    await page.waitForFunction(() => {
      const retryBtn = document.getElementById('set-foregroundFallbackRetry');
      return retryBtn && retryBtn.hidden;
    }, undefined, { timeout: 10000 });

    page.off('request', recordReqB);

    // 6. Verify editing becomes available only after both readiness conditions are satisfied
    console.log('Step B6: Verifying editing becomes available now that both conditions are satisfied...');
    const bFinalEditorHidden = await editor.getAttribute('hidden');
    const bFinalToggleDisabled = await toggle.isDisabled();
    const bFinalAddDisabled = await addBtn.isDisabled();
    console.log(`Final state: editorHidden=${bFinalEditorHidden !== null}, toggleDisabled=${bFinalToggleDisabled}, addDisabled=${bFinalAddDisabled}`);

    if (bFinalEditorHidden !== null) throw new Error('Editor should be visible after both conditions satisfied');
    if (bFinalToggleDisabled) throw new Error('Toggle should be enabled after recovery');
    if (bFinalAddDisabled) throw new Error('Add button should be enabled after recovery');

    const screenshot5Path = join(ARTIFACT_DIR, 'desktop-05-settings-ai-test-b-recovered.png');
    await page.screenshot({ path: screenshot5Path });
    testReport.screenshots.stepB6_recovered = screenshot5Path;
    console.log(`Saved screenshot 5 to ${screenshot5Path}`);

    testReport.testB.status = 'PASS';
    testReport.testB.evidence.push('Preferences write failed -> preferences unavailable (500) -> model catalog refreshed (200) -> editor remained disabled -> preferences unblocked & Retry clicked -> editor and controls enabled after both conditions satisfied.');

    // Mobile Viewport Layout Verification and Evidence Capture
    console.log('\n======================================================');
    console.log('Mobile Viewport Verification & Evidence Capture (390x844)');
    console.log('======================================================\n');
    await page.setViewportSize({ width: 390, height: 844 });
    await page.waitForTimeout(500);

    // Assert mobile layout: no document-level horizontal overflow and editor visible within viewport
    const mobileLayout = await page.evaluate(() => {
      const editorEl = document.getElementById('set-foregroundFallbackEditor');
      const docEl = document.documentElement;
      if (!editorEl) return { ok: false, reason: 'editor not found' };
      const hasDocOverflow = docEl.scrollWidth > docEl.clientWidth;
      const editorRect = editorEl.getBoundingClientRect();
      const editorVisible = editorRect.width > 0 && editorRect.height > 0;
      const fitsViewport = editorRect.right <= docEl.clientWidth + 2;
      return {
        ok: !hasDocOverflow && editorVisible && fitsViewport,
        hasDocOverflow,
        editorVisible,
        fitsViewport,
        docScrollWidth: docEl.scrollWidth,
        docClientWidth: docEl.clientWidth,
        editorWidth: editorRect.width,
      };
    });
    console.log(`Mobile layout check: ${JSON.stringify(mobileLayout)}`);
    if (!mobileLayout.ok) {
      throw new Error(`Mobile layout check failed: ${JSON.stringify(mobileLayout)}`);
    }

    const mobileScreenshotPath = join(ARTIFACT_DIR, 'mobile-01-settings-ai-fallback.png');
    await page.screenshot({ path: mobileScreenshotPath });
    testReport.screenshots.mobile_layout = mobileScreenshotPath;
    console.log(`Saved mobile screenshot to ${mobileScreenshotPath}`);

    // Stop tracing and save trace.zip
    const tracePath = join(ARTIFACT_DIR, 'playwright-trace.zip');
    await context.tracing.stop({ path: tracePath });
    testReport.tracePath = tracePath;
    console.log(`Saved Playwright trace to ${tracePath}`);

    // Isolation check
    if (preChromaCount !== null) {
      const postChromaCount = await fetchChromaToolCount('127.0.0.1', 8100);
      if (postChromaCount !== preChromaCount) {
        throw new Error(`ChromaDB isolation breach: count changed from ${preChromaCount} to ${postChromaCount}`);
      }
      console.log(`[isolation-check] Verified ChromaDB at 8100 tool count unchanged (${postChromaCount}). Zero mutations.`);
    }

    console.log('\n======================================================');
    console.log('ALL BROWSER REGRESSION TESTS COMPLETED SUCCESSFULLY!');
    console.log('======================================================\n');
    console.log(JSON.stringify(testReport, null, 2));

    return testReport;
  } finally {
    cleanup();
  }
}

const isMainScript = Boolean(
  process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))
);

if (isMainScript) {
  runBrowserTests().catch((err) => {
    console.error('Browser regression test failed:', err);
    cleanup();
    process.exit(1);
  });
}
