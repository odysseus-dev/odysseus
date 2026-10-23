// Tests against the running Odysseus app, not a static mock page.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { defineConfig } = require('@playwright/test');

const repositoryRoot = path.resolve(__dirname, '../..');
const port = Number(process.env.ODYSSEUS_LAYOUT_PORT || '17739');
const baseURL = `http://127.0.0.1:${port}`;
const pythonInVenv = path.join(repositoryRoot, '.venv', 'bin', 'python');
const python = process.env.ODYSSEUS_TEST_PYTHON || (fs.existsSync(pythonInVenv) ? pythonInVenv : 'python3');
const dataDirectory = fs.mkdtempSync(path.join(os.tmpdir(), 'odysseus-pr6396-'));
const evidenceDir = process.env.ODYSSEUS_LAYOUT_EVIDENCE_DIR || path.join(os.homedir(), 'Pictures', 'Odysseus-6396-followup');

module.exports = defineConfig({
  testDir: path.join(__dirname, 'ios-layout'),
  testMatch: ['**/*.spec.cjs'],
  outputDir: path.join(dataDirectory, 'playwright-output'),
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: 0,
  workers: 1,
  reporter: [['line']],
  use: {
    baseURL,
    headless: true,
    serviceWorkers: 'block',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: [
    { name: 'desktop-chromium', use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } } },
    { name: 'phone-webkit-portrait', use: { browserName: 'webkit', viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true } },
    { name: 'phone-webkit-landscape', use: { browserName: 'webkit', viewport: { width: 844, height: 390 }, hasTouch: true, isMobile: true } },
    { name: 'tablet-webkit', use: { browserName: 'webkit', viewport: { width: 820, height: 1000 }, hasTouch: true, isMobile: true } },
  ],
  metadata: { evidenceDir },
  webServer: {
    command: `${JSON.stringify(python)} -m uvicorn app:app --host 127.0.0.1 --port ${port}`,
    cwd: repositoryRoot,
    url: baseURL,
    timeout: 120_000,
    reuseExistingServer: false,
    env: {
      ...process.env,
      AUTH_ENABLED: 'false',
      // Never connect the disposable app to the host's real ChromaDB.
      CHROMADB_HOST: '127.0.0.1',
      CHROMADB_PORT: String(port + 1),
      LLM_HOST: '127.0.0.1',
      EMBEDDING_URL: `http://127.0.0.1:${port + 1}/v1/embeddings`,
      DATABASE_URL: `sqlite:///${path.join(dataDirectory, 'app.db')}`,
      ODYSSEUS_DATA_DIR: dataDirectory,
      ODYSSEUS_STARTUP_WARMUPS: '0',
      RESPONSE_COMPRESSION_ENABLED: 'false',
    },
  },
});
