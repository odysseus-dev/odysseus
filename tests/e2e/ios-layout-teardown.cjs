const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');

/**
 * Global teardown for the iOS/mobile layout Playwright test suite.
 * Cleans up only disposable temporary application data directories,
 * preserving diagnostic artifacts (screenshots and failure traces) in test-results/.
 */
module.exports = async () => {
  const dataDirectory = process.env.ODYSSEUS_LAYOUT_DATA_DIR;
  if (!dataDirectory || typeof dataDirectory !== 'string') return;

  try {
    const resolvedData = path.resolve(dataDirectory);
    const allowedTmpBases = [os.tmpdir(), '/var/tmp', '/tmp'].map(b => path.resolve(b));
    const isTmp = allowedTmpBases.some(base => resolvedData.startsWith(base + path.sep));
    const baseName = path.basename(resolvedData);

    if (isTmp && (baseName.startsWith('odysseus-ios-layout-') || baseName.startsWith('odysseus-pr6396-'))) {
      if (fs.existsSync(resolvedData)) {
        fs.rmSync(resolvedData, { recursive: true, force: true });
      }
    }
  } catch (err) {
    console.warn(`[ios-layout-teardown] Warning: failed to clean temporary runtime dir: ${err.message}`);
  }
};
