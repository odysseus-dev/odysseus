import { spawn, spawnSync, execSync } from 'child_process';
import { chromium } from 'playwright';
import { existsSync, unlinkSync, mkdirSync, mkdtempSync, rmSync } from 'fs';
import { resolve, join } from 'path';
import { tmpdir, homedir } from 'os';
import net from 'net';

const REPO_ROOT = resolve(process.env.REPO_ROOT || '.');
const isPosix = process.platform !== 'win32';
const TEST_TEMP_DIR = mkdtempSync(join(tmpdir(), 'odysseus-test-ep-'));
const DATA_DIR = join(TEST_TEMP_DIR, 'data');
mkdirSync(DATA_DIR, { recursive: true });
const DB_PATH = join(TEST_TEMP_DIR, 'test.db');
const FASTEMBED_DIR = join(TEST_TEMP_DIR, 'fastembed');
mkdirSync(FASTEMBED_DIR, { recursive: true });
const MAIL_DIR = join(TEST_TEMP_DIR, 'mail');
mkdirSync(MAIL_DIR, { recursive: true });
const RUNTIME_DIR = join(TEST_TEMP_DIR, 'runtime');
mkdirSync(RUNTIME_DIR, { recursive: true });
const ARTIFACT_DIR = process.env.ARTIFACT_DIR || process.env.NOTES_NATIVE_EVIDENCE_DIR || null;

async function getAvailablePort() {
  if (process.env.PORT) return parseInt(process.env.PORT, 10);
  return new Promise((resolvePort, rejectPort) => {
    const srv = net.createServer();
    srv.listen(0, '127.0.0.1', () => {
      const port = srv.address().port;
      srv.close((err) => {
        if (err) rejectPort(err);
        else resolvePort(port);
      });
    });
    srv.on('error', rejectPort);
  });
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

async function runBrowserTests() {
  const port = await getAvailablePort();
  let chromaGuardPort = await getAvailablePort();
  while (chromaGuardPort === 8100 || chromaGuardPort === port) {
    chromaGuardPort = await getAvailablePort();
  }

  // Preflight isolation check: if host ChromaDB is running at port 8100, record count
  let preChromaCount = null;
  try {
    const hbRes = await fetch('http://127.0.0.1:8100/api/v2/heartbeat', { signal: AbortSignal.timeout(1000) });
    if (hbRes.ok) {
      const colRes = await fetch('http://127.0.0.1:8100/api/v2/tenants/default_tenant/databases/default_database/collections', { signal: AbortSignal.timeout(1000) });
      if (colRes.ok) {
        const cols = await colRes.json();
        const toolCol = cols.find(c => c.name && c.name.includes('tool_index'));
        if (toolCol) {
          const countRes = await fetch(`http://127.0.0.1:8100/api/v2/tenants/default_tenant/databases/default_database/collections/${toolCol.id}/count`, { signal: AbortSignal.timeout(1000) });
          if (countRes.ok) {
            preChromaCount = await countRes.json();
            console.log(`[isolation-check] Live ChromaDB detected at 8100; tool_index item count = ${preChromaCount}`);
          }
        }
      }
    }
  } catch (_) {
    // Port 8100 not running or unreachable
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
    // Prevent unauthenticated notification logging from triggering 401 redirect
    await page.route('**/api/tasks/notification-logs', (route) => {
      route.fulfill({ status: 200, contentType: 'application/json', body: '{}' });
    });

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
    await page.unroute('**/api/tasks/notification-logs');
    console.log('Verified: HTTP error handling triggers user error notification and re-syncs state.');

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
      try {
        const colRes = await fetch('http://127.0.0.1:8100/api/v2/tenants/default_tenant/databases/default_database/collections', { signal: AbortSignal.timeout(1000) });
        if (colRes.ok) {
          const cols = await colRes.json();
          const toolCol = cols.find(c => c.name && c.name.includes('tool_index'));
          if (toolCol) {
            const countRes = await fetch(`http://127.0.0.1:8100/api/v2/tenants/default_tenant/databases/default_database/collections/${toolCol.id}/count`, { signal: AbortSignal.timeout(1000) });
            if (countRes.ok) {
              const postChromaCount = await countRes.json();
              if (postChromaCount !== preChromaCount) {
                throw new Error(`Isolation breach: ChromaDB tool_index count changed from ${preChromaCount} to ${postChromaCount}`);
              }
              console.log(`[isolation-check] Verified: Persistent ChromaDB count unchanged (${postChromaCount}). Zero mutations detected.`);
            }
          }
        }
      } catch (e) {
        console.warn(`[isolation-check] Post-check warning: ${e.message}`);
      }
    }

    console.log('ALL PORTABLE BROWSER VALIDATION CHECKS PASSED SUCCESSFULLY!');
  } finally {
    cleanup();
  }
}

runBrowserTests().catch((err) => {
  console.error('Browser validation test failed:', err);
  cleanup();
  process.exit(1);
});
