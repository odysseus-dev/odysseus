const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const { execFileSync } = require('node:child_process');

const repoRoot = path.resolve(__dirname, '..', '..');
const defaultResultsDir = path.join(repoRoot, 'test-results');
const defaultReportDir = path.join(repoRoot, 'playwright-report');
const defaultSanitizerScript = path.join(repoRoot, 'scripts', 'sanitize_trace_artifacts.py');

/**
 * Confine dynamic paths to explicitly allowed test directories.
 */
function validateTestPath(targetPath, allowedBases, label) {
  if (!targetPath || typeof targetPath !== 'string') return null;
  const resolved = path.resolve(targetPath);
  const isAllowed = allowedBases.some((base) => {
    const resolvedBase = path.resolve(base);
    return resolved.startsWith(resolvedBase + path.sep) || resolved === resolvedBase;
  });
  if (!isAllowed) {
    throw new Error(`Security rejection: ${label} path "${targetPath}" is outside allowed test directories`);
  }
  return resolved;
}

module.exports = async (options = {}) => {
  // If options is a Playwright FullConfig object, ignore it and use safe defaults
  const isPlaywrightConfig = options && (options.configFile || options.rootDir);
  const testOpts = (!isPlaywrightConfig && typeof options === 'object') ? options : {};

  // Confine cleanup of PHOTO_EDITOR_E2E_DATA_DIR:
  // Strictly require it to be within an OS temporary directory and match the expected test prefix
  const dataDirectory = process.env.PHOTO_EDITOR_E2E_DATA_DIR;
  if (dataDirectory && typeof dataDirectory === 'string') {
    try {
      const resolvedData = path.resolve(dataDirectory);
      const allowedTmpBases = [os.tmpdir(), '/var/tmp', '/tmp'].map((b) => path.resolve(b));
      const isTmp = allowedTmpBases.some((base) => resolvedData.startsWith(base + path.sep));
      const baseName = path.basename(resolvedData);
      if (isTmp && baseName.startsWith('odysseus-photo-editor-e2e-')) {
        fs.rmSync(resolvedData, { recursive: true, force: true });
      }
    } catch (_) {}
  }

  // Secure default tracing: never publish trace archives in CI or without explicit opt-in.
  // When traces are not opted-in, purge all archives unconditionally.
  // When local opt-in tracing is active, run the strict fail-closed structured sanitizer.
  const allowedBases = [defaultResultsDir, defaultReportDir, os.tmpdir(), '/var/tmp', '/tmp'];
  const resultsDir = testOpts.resultsDir
    ? validateTestPath(testOpts.resultsDir, allowedBases, 'resultsDir')
    : defaultResultsDir;
  const reportDir = testOpts.reportDir
    ? validateTestPath(testOpts.reportDir, allowedBases, 'reportDir')
    : defaultReportDir;

  if (fs.existsSync(reportDir)) {
    try {
      fs.rmSync(reportDir, { recursive: true, force: true });
    } catch (_) {}
  }

  if (fs.existsSync(resultsDir)) {
    const purgeSensitiveArtifacts = (dir) => {
      if (!fs.existsSync(dir)) return;
      const entries = fs.readdirSync(dir, { withFileTypes: true });
      for (const entry of entries) {
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          purgeSensitiveArtifacts(fullPath);
        } else if (entry.isFile() && (
          entry.name.endsWith('.zip') ||
          entry.name.endsWith('.tmp') ||
          entry.name.endsWith('.png') ||
          entry.name.endsWith('.har')
        )) {
          try {
            fs.rmSync(fullPath, { force: true });
          } catch (_) {}
        }
      }
    };

    const purgeZipAndTmp = (dir) => {
      if (!fs.existsSync(dir)) return;
      const entries = fs.readdirSync(dir, { withFileTypes: true });
      for (const entry of entries) {
        const fullPath = path.join(dir, entry.name);
        if (entry.isDirectory()) {
          purgeZipAndTmp(fullPath);
        } else if (entry.isFile() && (entry.name.endsWith('.zip') || entry.name.endsWith('.tmp'))) {
          try {
            fs.rmSync(fullPath, { force: true });
          } catch (_) {}
        }
      }
    };

    const isCI = Boolean(process.env.CI) || Boolean(process.env.GITHUB_ACTIONS);
    const traceOptIn = !isCI && ['1', 'true'].includes(String(process.env.PLAYWRIGHT_ENABLE_TRACE || '').toLowerCase());

    if (isCI || !traceOptIn) {
      // In CI or default local runs, purge all sensitive trace archives and artifacts
      purgeSensitiveArtifacts(resultsDir);
    } else {
      const python = process.env.ODYSSEUS_TEST_PYTHON || 'python';
      const sanitizerScript = testOpts.sanitizerScript
        ? validateTestPath(testOpts.sanitizerScript, [path.join(repoRoot, 'scripts'), os.tmpdir(), '/var/tmp', '/tmp'], 'sanitizerScript')
        : defaultSanitizerScript;
      const rawTimeout = Number(testOpts.timeout) || 30_000;
      const timeout = Math.max(100, Math.min(rawTimeout, 120_000));
      let sanitizerSucceeded = false;
      try {
        execFileSync(python, [sanitizerScript, resultsDir], {
          stdio: 'ignore',
          timeout,
          shell: false,
        });
        sanitizerSucceeded = true;
      } catch (_) {
        sanitizerSucceeded = false;
      }

      if (!sanitizerSucceeded) {
        // Fail closed: purge all remaining zip or tmp archives
        purgeZipAndTmp(resultsDir);
      }
    }
  }
};
