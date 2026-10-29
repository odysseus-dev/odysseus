const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { defineConfig } = require('@playwright/test');
const { disposableCIArtifacts, marker } = require('./artifact-policy');
const isCI = Boolean(process.env.CI) || Boolean(process.env.GITHUB_ACTIONS);
const disposableOptIn = isCI && process.env.ODYSSEUS_E2E_DISPOSABLE_ARTIFACTS === '1';
if (disposableOptIn && (process.env.PHOTO_EDITOR_E2E_DATA_DIR || process.env.PHOTO_EDITOR_E2E_DB_PATH || process.env.PHOTO_EDITOR_E2E_AUTH === '0') && !disposableCIArtifacts()) {
  throw new Error('CI artifact opt-in requires fresh authenticated fixture paths');
}

const port = Number(process.env.PHOTO_EDITOR_E2E_PORT || 7013);
const baseURL = `http://127.0.0.1:${port}`;
const repositoryRoot = path.join(__dirname, '..', '..');
const defaultPython = process.platform === 'win32'
  ? path.join(repositoryRoot, '.venv', 'Scripts', 'python.exe')
  : path.join(repositoryRoot, '.venv', 'bin', 'python');
const python = process.env.ODYSSEUS_TEST_PYTHON || (fs.existsSync(defaultPython) ? defaultPython : 'python');
const tmpBase = fs.existsSync('/var/tmp') ? '/var/tmp' : os.tmpdir();
const dataDirectory = process.env.PHOTO_EDITOR_E2E_DATA_DIR
  || fs.mkdtempSync(path.join(tmpBase, 'odysseus-photo-editor-e2e-'));
const databasePath = process.env.PHOTO_EDITOR_E2E_DB_PATH
  || path.join(dataDirectory, 'app.db');
const authenticated = process.env.PHOTO_EDITOR_E2E_AUTH !== '0';
process.env.PHOTO_EDITOR_E2E_DB_PATH = databasePath;
process.env.PHOTO_EDITOR_E2E_DATA_DIR = dataDirectory;

const browserName = process.env.PHOTO_EDITOR_E2E_BROWSER || 'chromium';
if (!['chromium', 'firefox', 'webkit'].includes(browserName)) {
  throw new Error(`Unsupported PHOTO_EDITOR_E2E_BROWSER: ${browserName}`);
}

if (disposableOptIn) {
  fs.writeFileSync(path.join(dataDirectory, marker), JSON.stringify({ synthetic: true, authenticated: true, database: databasePath }));
}
const traceOptIn = !isCI && ['1', 'true'].includes(String(process.env.PLAYWRIGHT_ENABLE_TRACE || '').toLowerCase());

module.exports = defineConfig({
  testDir: path.join(__dirname, 'photo-editor'),
  outputDir: path.join(__dirname, '..', '..', 'test-results', 'photo-editor'),
  timeout: 90_000,
  expect: { timeout: 10_000 },
  forbidOnly: isCI,
  retries: 0,
  fullyParallel: false,
  workers: 1,
  reporter: isCI ? 'line' : 'line',
  globalSetup: require.resolve('./setup.js'),
  globalTeardown: require.resolve('./teardown.js'),
  use: {
    baseURL,
    browserName,
    headless: true,
    launchOptions: {
      args: ['--disable-dev-shm-usage', '--no-sandbox'],
    },
    viewport: { width: 1440, height: 960 },
    extraHTTPHeaders: { 'Accept-Encoding': 'identity' },
    serviceWorkers: 'block',
    ...(authenticated ? { storageState: path.join(dataDirectory, 'browser-auth.json') } : {}),
    screenshot: 'only-on-failure',
    // Default CI suppression remains; only the fresh synthetic fixture opts in.
    // Local developer tracing requires explicit PLAYWRIGHT_ENABLE_TRACE=1.
    trace: (traceOptIn || disposableCIArtifacts()) ? 'retain-on-failure' : 'off',
  },
  webServer: {
    command: `${JSON.stringify(python)} -m uvicorn app:app --host 127.0.0.1 --port ${port}`,
    cwd: repositoryRoot,
    url: baseURL,
    timeout: 120_000,
    reuseExistingServer: false,
    env: {
      ...process.env,
      AUTH_ENABLED: authenticated ? 'true' : 'false',
      DATABASE_URL: `sqlite:///${databasePath}`,
      ODYSSEUS_DATA_DIR: dataDirectory,
      ODYSSEUS_STARTUP_WARMUPS: '0',
      ODYSSEUS_TOOL_INDEX_PREWARM: '0',
      ODYSSEUS_BROWSER_MCP_REQUIRE_CACHE: '1',
      npm_config_offline: 'true',
      npm_config_cache: path.join(dataDirectory, 'npm-cache'),
      RESPONSE_COMPRESSION_ENABLED: 'false',
    },
  },
});
