import { spawn, spawnSync, execSync } from 'child_process';
import { chromium } from 'playwright';
import { existsSync, unlinkSync, mkdirSync, mkdtempSync, rmSync } from 'fs';
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
const ARTIFACT_DIR = process.env.ARTIFACT_DIR || process.env.NOTES_NATIVE_EVIDENCE_DIR || null;

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
    process.env.PYTHON,
    process.env.ODYSSEUS_TEST_PYTHON,
    process.env.VIRTUAL_ENV ? join(process.env.VIRTUAL_ENV, 'bin', 'python') : null,
    join(REPO_ROOT, '..', '..', 'dev', 'venvs', 'main', 'bin', 'python'),
    join(REPO_ROOT, 'venv', 'bin', 'python'),
    join(REPO_ROOT, '.venv', 'bin', 'python'),
    join(homedir(), '.cache', 'odysseus-ci-baseline-py311', 'bin', 'python'),
    homedir().includes('.local/share') ? join(homedir().split('/.local/share')[0], '.cache', 'odysseus-ci-baseline-py311', 'bin', 'python') : null,
    'python3',
    'python',
  ].filter(Boolean);

  for (const cand of candidates) {
    try {
      const res = spawnSync(cand, ['-c', 'import uvicorn'], { stdio: 'ignore' });
      if (res.status === 0) return cand;
    } catch (_) {}
  }
  return 'python3';
}

function initTestEnvironment() {
  TEST_TEMP_DIR = mkdtempSync(join(tmpdir(), 'odysseus-test-ep-'));
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
      model_refresh_mode TEXT DEFAULT 'auto',
      model_refresh_interval INTEGER,
      model_refresh_timeout INTEGER,
      supports_tools INTEGER,
      model_tool_modes TEXT,
      owner TEXT,
      provider_auth_id TEXT,
      created_at DATETIME DEFAULT CURRENT_TIMESTAMP,
      updated_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );

    INSERT INTO model_endpoints (id, name, base_url, model_type, is_enabled, supports_tools)
    VALUES ('ep-ollama-local', 'Ollama Local (Qwen)', 'http://127.0.0.1:11434/v1', 'llm', 1, NULL);

    INSERT INTO model_endpoints (id, name, base_url, model_type, is_enabled, supports_tools)
    VALUES ('ep-flux-image', 'Flux Dev Image Server', 'http://127.0.0.1:8080/v1', 'image', 1, NULL);

    INSERT INTO model_endpoints (id, name, base_url, model_type, is_enabled, supports_tools)
    VALUES ('ep-chatgpt-sub', 'ChatGPT Subscription', 'https://chatgpt.com/backend-api/codex', 'llm', 1, 0);
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

  // Preflight isolation check: if host ChromaDB is running at port 8100, record count
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
    if (s) console.error(`[server err] ${s}`);
  });

  const baseUrl = `http://127.0.0.1:${port}`;
  await waitForServer(`${baseUrl}/`);
  console.log(`Server is healthy at ${baseUrl}. Launching Playwright browser...`);

  browserInstance = await chromium.launch({ headless: true });

  try {
    // ── Desktop Layout & Interaction ─────────────────────────────────────────
    console.log('Testing Desktop layout (1280x800)...');
    const desktopContext = await browserInstance.newContext({ viewport: { width: 1280, height: 800 } });
    const page = await desktopContext.newPage();
    page.on('pageerror', (err) => console.error('[browser page error]', err));
    await page.route('**/api/tasks/notification-logs', (route) => {
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });

    await page.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' });
    await page.waitForTimeout(800);

    // Open Settings -> Added Models
    await page.evaluate(() => {
      if (window.adminModule && window.adminModule.open) {
        window.adminModule.open('added-models');
      } else if (window.settingsModule && window.settingsModule.open) {
        window.settingsModule.open('added-models');
      }
    });

    await page.waitForSelector('[data-adm-ep-id="ep-ollama-local"]', { timeout: 10000 });
    console.log('Added Models panel rendered.');

    // 1. Verify button rendering, attributes, and suppression
    const toolsBtn = page.locator('[data-adm-tools-ep="ep-ollama-local"]');
    if (!(await toolsBtn.isVisible())) {
      throw new Error('Expected tools toggle button on ep-ollama-local, but not found');
    }

    const btnType = await toolsBtn.getAttribute('type');
    const btnAria = await toolsBtn.getAttribute('aria-label');
    const btnTitle = await toolsBtn.getAttribute('title');
    const initialText = (await toolsBtn.textContent()).trim();
    console.log(`Initial button state: "${initialText}" (type="${btnType}", aria-label="${btnAria}")`);

    if (btnType !== 'button') throw new Error(`Expected type="button", got ${btnType}`);
    if (initialText !== 'Tools: Auto') throw new Error(`Expected "Tools: Auto", got "${initialText}"`);
    if (!btnAria || !btnAria.includes('Auto')) throw new Error(`Expected aria-label with "Auto", got "${btnAria}"`);
    if (!btnTitle || !btnTitle.includes('Native tool calling')) throw new Error('Expected descriptive title attribute');

    // 2. Verify suppression on unsupported endpoints
    const imageToolsBtn = page.locator('[data-adm-tools-ep="ep-flux-image"]');
    if ((await imageToolsBtn.count()) > 0) {
      throw new Error('Image endpoint must NOT render tools toggle button');
    }
    console.log('Verified: Image endpoint correctly omits tools toggle.');

    const chatgptToolsBtn = page.locator('[data-adm-tools-ep="ep-chatgpt-sub"]');
    if ((await chatgptToolsBtn.count()) > 0) {
      throw new Error('ChatGPT subscription endpoint must NOT render tools toggle button');
    }
    console.log('Verified: ChatGPT subscription endpoint correctly omits tools toggle.');

    // 3. Test tri-state cycling and focus restoration
    // Click 1: Auto -> On
    await toolsBtn.click();
    await page.waitForTimeout(400);
    const textAfterClick1 = (await page.locator('[data-adm-tools-ep="ep-ollama-local"]').textContent()).trim();
    console.log(`After click 1: "${textAfterClick1}"`);
    if (textAfterClick1 !== 'Tools: On') throw new Error(`Expected "Tools: On", got "${textAfterClick1}"`);

    let activeEp = await page.evaluate(() => document.activeElement?.getAttribute('data-adm-tools-ep'));
    if (activeEp !== 'ep-ollama-local') {
      throw new Error(`Expected focus restored to ep-ollama-local button, got: ${activeEp}`);
    }

    // Click 2: On -> Off
    await page.locator('[data-adm-tools-ep="ep-ollama-local"]').click();
    await page.waitForTimeout(400);
    const textAfterClick2 = (await page.locator('[data-adm-tools-ep="ep-ollama-local"]').textContent()).trim();
    console.log(`After click 2: "${textAfterClick2}"`);
    if (textAfterClick2 !== 'Tools: Off') throw new Error(`Expected "Tools: Off", got "${textAfterClick2}"`);

    activeEp = await page.evaluate(() => document.activeElement?.getAttribute('data-adm-tools-ep'));
    if (activeEp !== 'ep-ollama-local') {
      throw new Error(`Expected focus restored to ep-ollama-local button, got: ${activeEp}`);
    }

    // Click 3: Off -> Auto
    await page.locator('[data-adm-tools-ep="ep-ollama-local"]').click();
    await page.waitForTimeout(400);
    const textAfterClick3 = (await page.locator('[data-adm-tools-ep="ep-ollama-local"]').textContent()).trim();
    console.log(`After click 3: "${textAfterClick3}"`);
    if (textAfterClick3 !== 'Tools: Auto') throw new Error(`Expected "Tools: Auto", got "${textAfterClick3}"`);

    activeEp = await page.evaluate(() => document.activeElement?.getAttribute('data-adm-tools-ep'));
    if (activeEp !== 'ep-ollama-local') {
      throw new Error(`Expected focus restored to ep-ollama-local button, got: ${activeEp}`);
    }
    console.log('Verified: Tri-state cycle and focus restoration working across all transitions.');

    // 4. Test in-flight concurrency protection (button disabled while fetch pending)
    let resolvePendingPatch = null;
    await page.route('**/api/model-endpoints/*', async (route) => {
      if (route.request().method() === 'PATCH') {
        await new Promise((r) => { resolvePendingPatch = r; });
      }
      await route.continue();
    });

    // Initiate click without waiting for completion
    const clickPromise = page.locator('[data-adm-tools-ep="ep-ollama-local"]').click();
    await page.waitForTimeout(100);

    const isBtnDisabled = await page.locator('[data-adm-tools-ep="ep-ollama-local"]').isDisabled();
    console.log(`Button disabled state during in-flight PATCH: ${isBtnDisabled}`);
    if (!isBtnDisabled) {
      throw new Error('Expected button to be disabled while PATCH request is in-flight');
    }

    if (resolvePendingPatch) resolvePendingPatch();
    await clickPromise;
    // Wait for the re-render after PATCH to complete
    await page.waitForFunction(() => {
      const b = document.querySelector('[data-adm-tools-ep="ep-ollama-local"]');
      return b && !b.disabled;
    });
    await page.unroute('**/api/model-endpoints/*');
    console.log('Verified: In-flight concurrency protection disables button during requests.');

    // 5. Test HTTP error recovery & UI re-sync
    // Mock 403 response for the next PATCH
    await page.route('**/api/model-endpoints/*', async (route) => {
      if (route.request().method() === 'PATCH') {
        await route.fulfill({
          status: 403,
          contentType: 'application/json',
          body: JSON.stringify({ detail: 'Administrator privilege required' }),
        });
        return;
      }
      await route.continue();
    });

    const preErrorText = (await page.locator('[data-adm-tools-ep="ep-ollama-local"]').textContent()).trim();
    await Promise.all([
      page.waitForResponse((res) => res.url().includes('/api/model-endpoints') && res.status() === 403),
      page.locator('[data-adm-tools-ep="ep-ollama-local"]').click(),
    ]);

    // Wait for the button to re-enable after loadEndpoints in finally
    await page.waitForFunction(() => {
      const b = document.querySelector('[data-adm-tools-ep="ep-ollama-local"]');
      return b && !b.disabled;
    });

    // Verify toast error notification was rendered
    await page.waitForFunction(
      () => {
        const t = document.getElementById('toast');
        return t && t.classList.contains('show') && t.textContent.includes('Administrator privilege required');
      },
      { timeout: 5000 }
    );
    const reportedError = await page.evaluate(() => document.getElementById('toast')?.textContent.trim());
    console.log(`Reported UI toast error on 403: "${reportedError}"`);

    // Verify UI re-synced to persistent server state (did not stay in optimistic state)
    const postErrorText = (await page.locator('[data-adm-tools-ep="ep-ollama-local"]').textContent()).trim();
    console.log(`Post-error button label after re-sync: "${postErrorText}"`);
    if (postErrorText !== preErrorText) {
      throw new Error(`Expected button to re-sync back to "${preErrorText}", got: "${postErrorText}"`);
    }
    await page.unroute('**/api/model-endpoints/*');
    console.log('Verified: HTTP error handling triggers user error notification and re-syncs state.');

    // 6. Test network rejection during PATCH
    console.log('Testing network rejection during PATCH...');
    await page.route('**/api/model-endpoints/*', async (route) => {
      if (route.request().method() === 'PATCH') {
        await route.abort('failed');
        return;
      }
      await route.continue();
    });

    const preNetErrorText = (await page.locator('[data-adm-tools-ep="ep-ollama-local"]').textContent()).trim();
    await page.locator('[data-adm-tools-ep="ep-ollama-local"]').click();
    await page.waitForTimeout(400);

    // Verify button is re-enabled despite network rejection
    await page.waitForFunction(() => {
      const b = document.querySelector('[data-adm-tools-ep="ep-ollama-local"]');
      return b && !b.disabled;
    });

    const postNetErrorText = (await page.locator('[data-adm-tools-ep="ep-ollama-local"]').textContent()).trim();
    if (postNetErrorText !== preNetErrorText) {
      throw new Error(`Expected button state to remain "${preNetErrorText}" after network rejection, got: "${postNetErrorText}"`);
    }
    await page.unroute('**/api/model-endpoints/*');
    console.log('Verified: Network rejection during PATCH is handled, button is re-enabled, and focus is restored.');

    // 7. Successful PATCH + failed reload must preserve the persisted state.
    console.log('Testing persisted state after endpoint reload failure...');

    const toggleSelector = '[data-adm-tools-ep="ep-ollama-local"]';

    // Previous tests may leave the endpoint On or Off.
    // Restore Auto through the real UI before exercising this regression.
    for (let attempt = 0; attempt < 3; attempt++) {
      const previous = await page.locator(toggleSelector)
        .getAttribute('data-adm-tools-state');

      if (previous === 'auto') break;

      await page.locator(toggleSelector).click();

      await page.waitForFunction(({ selector, previous }) => {
        const button = document.querySelector(selector);
        return button && !button.disabled &&
          button.dataset.admToolsState !== previous;
      }, { selector: toggleSelector, previous });
    }

    const startingState = await page.locator(toggleSelector)
      .getAttribute('data-adm-tools-state');

    if (startingState !== 'auto') {
      throw new Error(`Expected Auto before regression, got ${startingState}`);
    }

    const observedPayloads = [];

    await page.route('**/api/model-endpoints/*', async (route) => {
      if (route.request().method() === 'PATCH') {
        const body = route.request().postDataJSON();
        observedPayloads.push(body.supports_tools);
      }
      await route.continue();
    });

    // PATCH reaches the real server, but the follow-up GET fails.
    await page.route('**/api/model-endpoints', async (route) => {
      if (route.request().method() === 'GET') {
        await route.abort('failed');
        return;
      }
      await route.continue();
    });

    await page.locator(toggleSelector).click();

    await page.waitForFunction((selector) => {
      const button = document.querySelector(selector);
      return button && !button.disabled &&
        button.dataset.admToolsState === 'on' &&
        button.textContent.trim() === 'Tools: On' &&
        button.getAttribute('aria-label') === 'Native tool calling: On';
    }, toggleSelector);

    if (observedPayloads.length !== 1 ||
        observedPayloads[0] !== true) {
      throw new Error(
        `Expected first PATCH supports_tools=true, got ${JSON.stringify(observedPayloads)}`
      );
    }

    console.log('Verified: Successful PATCH retained Tools: On despite failed GET.');

    // Restore connectivity and perform the next real toggle.
    await page.unroute('**/api/model-endpoints');

    await page.locator(toggleSelector).click();

    await page.waitForFunction((selector) => {
      const button = document.querySelector(selector);
      return button && !button.disabled &&
        button.dataset.admToolsState === 'off' &&
        button.textContent.trim() === 'Tools: Off';
    }, toggleSelector);

    if (observedPayloads.length !== 2 ||
        observedPayloads[1] !== false) {
      throw new Error(
        `Expected second PATCH supports_tools=false, got ${JSON.stringify(observedPayloads)}`
      );
    }

    await page.unroute('**/api/model-endpoints/*');

    // Verify the final value was persisted, not merely rendered.
    const persistedResponse = await page.request.get(
      `${baseUrl}/api/model-endpoints`
    );

    if (!persistedResponse.ok()) {
      throw new Error(`Endpoint verification GET failed: ${persistedResponse.status()}`);
    }

    const persistedEndpoints = await persistedResponse.json();
    const persistedEndpoint = persistedEndpoints.find(
      (ep) => ep.id === 'ep-ollama-local'
    );

    if (!persistedEndpoint || persistedEndpoint.supports_tools !== false) {
      throw new Error('Expected persisted supports_tools=false after retry');
    }

    console.log(
      'Verified: Auto -> On (reload failed) -> Off; ' +
      'PATCH payloads, UI state, and persistence are correct.'
    );

    // 8. Ordinary endpoint refresh must not reject when its GET fails.
    console.log('Testing non-throwing endpoint refresh...');

    await page.evaluate(() => {
      window.__endpointLoadUnhandled = [];
      window.addEventListener('unhandledrejection', (event) => {
        window.__endpointLoadUnhandled.push(String(event.reason));
      });
    });

    await page.route('**/api/model-endpoints', async (route) => {
      if (route.request().method() === 'GET') {
        await route.abort('failed');
        return;
      }
      await route.continue();
    });

    try {
      // This click calls loadEndpoints() without awaiting its result.
      // A rejected promise here was the original regression.
      await Promise.all([
        page.waitForEvent('requestfailed', {
          predicate: (request) =>
            new URL(request.url()).pathname === '/api/model-endpoints' &&
            request.method() === 'GET',
          timeout: 10000,
        }),
        page.locator('#adm-epSelectBtn').click(),
      ]);

      // Allow the unhandledrejection event to be dispatched if present.
      await page.waitForTimeout(200);

      const unhandled = await page.evaluate(
        () => window.__endpointLoadUnhandled
      );

      if (unhandled.length !== 0) {
        throw new Error(
          `Ordinary endpoint refresh produced unhandled rejections: ${JSON.stringify(unhandled)}`
        );
      }

      if (await page.locator('[data-adm-ep-id="ep-ollama-local"]').count() !== 1) {
        throw new Error('Failed refresh removed the previously mounted endpoint');
      }
    } finally {
      await page.unroute('**/api/model-endpoints');
    }

    // Select is hidden while selection mode is active.
    // Cancel exits selection mode and reloads after connectivity returns.
    await page.locator('#adm-epCancelSelectBtn').click();

    if (!(await page.locator('#adm-epSelectBtn').isVisible())) {
      throw new Error('Select button was not restored after cancellation');
    }

    await page.waitForFunction(() => {
      const button = document.querySelector(
        '[data-adm-tools-ep="ep-ollama-local"]'
      );
      return button && !button.disabled;
    });

    console.log(
      'Verified: Ordinary endpoint refresh failure preserves the DOM ' +
      'without unhandled promise rejections.'
    );

    // Capture desktop screenshot if evidence directory configured
    if (ARTIFACT_DIR) {
      try {
        mkdirSync(ARTIFACT_DIR, { recursive: true });
        const desktopScreenshotPath = join(ARTIFACT_DIR, 'added-models-desktop.png');
        await page.screenshot({ path: desktopScreenshotPath });
        console.log(`Saved desktop screenshot to ${desktopScreenshotPath}`);
      } catch (e) {
        console.warn(`Could not save desktop screenshot: ${e.message}`);
      }
    }

    // ── Mobile Viewport Test (390x844) ───────────────────────────────────────
    console.log('Testing Mobile viewport (390x844)...');
    const mobileContext = await browserInstance.newContext({ viewport: { width: 390, height: 844 }, isMobile: true });
    const mobilePage = await mobileContext.newPage();

    await mobilePage.goto(`${baseUrl}/`, { waitUntil: 'domcontentloaded' });
    await mobilePage.waitForTimeout(800);

    await mobilePage.evaluate(() => {
      if (window.adminModule && window.adminModule.open) {
        window.adminModule.open('added-models');
      } else if (window.settingsModule && window.settingsModule.open) {
        window.settingsModule.open('added-models');
      }
    });

    await mobilePage.waitForSelector('[data-adm-ep-id="ep-ollama-local"]', { timeout: 10000 });
    const mobileToolsBtn = mobilePage.locator('[data-adm-tools-ep="ep-ollama-local"]');
    if (!(await mobileToolsBtn.isVisible())) {
      throw new Error('Mobile tools button should be visible in Added Models');
    }

    const mobilePreClick = (await mobileToolsBtn.textContent()).trim();
    await mobileToolsBtn.click();
    await mobilePage.waitForTimeout(400);

    const mobilePostClick = (await mobilePage.locator('[data-adm-tools-ep="ep-ollama-local"]').textContent()).trim();
    console.log(`Mobile toggle state: "${mobilePreClick}" -> "${mobilePostClick}"`);
    if (mobilePostClick === mobilePreClick) {
      throw new Error(`Mobile tap failed to change button state from "${mobilePreClick}"`);
    }

    if (ARTIFACT_DIR) {
      try {
        mkdirSync(ARTIFACT_DIR, { recursive: true });
        const mobileScreenshotPath = join(ARTIFACT_DIR, 'added-models-mobile.png');
        await mobilePage.screenshot({ path: mobileScreenshotPath });
        console.log(`Saved mobile screenshot to ${mobileScreenshotPath}`);
      } catch (e) {
        console.warn(`Could not save mobile screenshot: ${e.message}`);
      }
    }

    // Post-test isolation verification: if live ChromaDB was present, assert count is unchanged
    if (preChromaCount !== null) {
      const postChromaCount = await fetchChromaToolCount('127.0.0.1', 8100);
      if (postChromaCount === null) {
        console.warn('[isolation-check] Live ChromaDB at 8100 became unreachable during post-test check.');
      } else if (postChromaCount !== preChromaCount) {
        throw new Error(`Isolation breach: ChromaDB tool_index count changed from ${preChromaCount} to ${postChromaCount}`);
      } else {
        console.log(`[isolation-check] Verified: Persistent ChromaDB count unchanged (${postChromaCount}). Zero mutations detected.`);
      }
    }

    console.log('ALL PORTABLE BROWSER VALIDATION CHECKS PASSED SUCCESSFULLY!');
  } finally {
    cleanup();
  }
}

const isMainScript = Boolean(
  process.argv[1] && resolve(process.argv[1]) === resolve(fileURLToPath(import.meta.url))
);

if (isMainScript) {
  runBrowserTests().catch((err) => {
    console.error('Browser validation test failed:', err);
    cleanup();
    process.exit(1);
  });
}
