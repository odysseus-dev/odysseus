const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const marker = '.disposable-ci-fixture.json';

function disposableCIArtifacts() {
  if (!(process.env.CI || process.env.GITHUB_ACTIONS) || process.env.ODYSSEUS_E2E_DISPOSABLE_ARTIFACTS !== '1') return false;
  const directory = process.env.PHOTO_EDITOR_E2E_DATA_DIR;
  if (!directory || process.env.PHOTO_EDITOR_E2E_AUTH === '0') return false;
  const resolved = path.resolve(directory);
  if (!path.basename(resolved).startsWith('odysseus-photo-editor-e2e-')) return false;
  if (![os.tmpdir(), '/tmp', '/var/tmp'].some(base => resolved.startsWith(path.resolve(base) + path.sep))) return false;
  if (process.env.PHOTO_EDITOR_E2E_DB_PATH !== path.join(resolved, 'app.db')) return false;
  try {
    const fixture = JSON.parse(fs.readFileSync(path.join(resolved, marker), 'utf8'));
    return fixture.synthetic === true && fixture.authenticated === true && fixture.database === path.join(resolved, 'app.db');
  } catch { return false; }
}
module.exports = { disposableCIArtifacts, marker };
