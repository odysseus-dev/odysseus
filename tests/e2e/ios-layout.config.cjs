// Tests against the running Odysseus app, not a static mock page.
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { defineConfig } = require('@playwright/test');

const repositoryRoot = path.resolve(__dirname, '../..');
const port = Number(process.env.ODYSSEUS_LAYOUT_PORT || '17739');
const baseURL = `http://127.0.0.1:${port}`;
const defaultPython = process.platform === 'win32'
  ? path.join(repositoryRoot, '.venv', 'Scripts', 'python.exe')
  : path.join(repositoryRoot, '.venv', 'bin', 'python');
const python = process.env.ODYSSEUS_TEST_PYTHON
  || (fs.existsSync(defaultPython) ? defaultPython : 'python3');

const dataDirectory = process.env.ODYSSEUS_LAYOUT_DATA_DIR
  || fs.mkdtempSync(path.join(os.tmpdir(), 'odysseus-ios-layout-'));
process.env.ODYSSEUS_LAYOUT_DATA_DIR = dataDirectory;

// Default evidence directory to repository-relative test-results/ios-layout
// while preserving the environment-variable override if provided.
const evidenceDir = process.env.ODYSSEUS_LAYOUT_EVIDENCE_DIR
  || path.join(repositoryRoot, 'test-results', 'ios-layout');
const outputDir = path.join(repositoryRoot, 'test-results', 'ios-layout-traces');

// Emergency process-exit cleanup for disposable application runtime state
const cleanDataDirSync = () => {
  const dir = process.env.ODYSSEUS_LAYOUT_DATA_DIR;
  if (!dir || typeof dir !== 'string') return;
  try {
    const resolved = path.resolve(dir);
    const allowed = [os.tmpdir(), '/var/tmp', '/tmp'].map(b => path.resolve(b));
    if (allowed.some(base => resolved.startsWith(base + path.sep))) {
      const baseName = path.basename(resolved);
      if (baseName.startsWith('odysseus-ios-layout-') || baseName.startsWith('odysseus-pr6396-')) {
        if (fs.existsSync(resolved)) {
          fs.rmSync(resolved, { recursive: true, force: true });
        }
      }
    }
  } catch (_) {}
};

process.once('SIGINT', () => { cleanDataDirSync(); process.exit(130); });
process.once('SIGTERM', () => { cleanDataDirSync(); process.exit(143); });

function isWebKitInstalled() {
  try {
    const { webkit } = require('@playwright/test');
    return fs.existsSync(webkit.executablePath());
  } catch (_) {
    return false;
  }
}

const chromiumProjects = [
  {
    name: 'desktop-chromium',
    use: { browserName: 'chromium', viewport: { width: 1440, height: 900 } },
  },
  {
    name: 'phone-chromium-portrait',
    use: { browserName: 'chromium', viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true },
  },
  {
    name: 'phone-chromium-landscape',
    use: { browserName: 'chromium', viewport: { width: 844, height: 390 }, hasTouch: true, isMobile: true },
  },
  {
    name: 'tablet-chromium',
    use: { browserName: 'chromium', viewport: { width: 820, height: 1000 }, hasTouch: true, isMobile: true },
  },
];

const webkitProjects = [
  {
    name: 'phone-webkit-portrait',
    use: { browserName: 'webkit', viewport: { width: 390, height: 844 }, hasTouch: true, isMobile: true },
  },
  {
    name: 'phone-webkit-landscape',
    use: { browserName: 'webkit', viewport: { width: 844, height: 390 }, hasTouch: true, isMobile: true },
  },
  {
    name: 'tablet-webkit',
    use: { browserName: 'webkit', viewport: { width: 820, height: 1000 }, hasTouch: true, isMobile: true },
  },
];

// Determine active projects based on environment and local browser availability
const requestedBrowser = (process.env.ODYSSEUS_LAYOUT_BROWSER || 'all').toLowerCase();
let selectedProjects = [];

if (requestedBrowser === 'chromium') {
  selectedProjects = chromiumProjects;
} else if (requestedBrowser === 'webkit') {
  selectedProjects = webkitProjects;
} else {
  // 'all': Include chromium always; include webkit if installed or if explicitly targeted via cli args
  const hasCliWebKitTarget = process.argv.some(arg => arg.toLowerCase().includes('webkit'));
  if (isWebKitInstalled() || hasCliWebKitTarget) {
    selectedProjects = [...chromiumProjects, ...webkitProjects];
  } else {
    // Only Chromium is available locally (e.g. standard CI / default runner)
    selectedProjects = chromiumProjects;
  }
}

module.exports = defineConfig({
  testDir: path.join(__dirname, 'ios-layout'),
  testMatch: ['**/*.spec.cjs'],
  outputDir,
  timeout: 90_000,
  expect: { timeout: 15_000 },
  retries: 0,
  workers: 1,
  reporter: [['line']],
  globalTeardown: require.resolve('./ios-layout-teardown.cjs'),
  use: {
    baseURL,
    headless: true,
    serviceWorkers: 'block',
    screenshot: 'only-on-failure',
    trace: 'retain-on-failure',
  },
  projects: selectedProjects,
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
