const fs = require('node:fs');
const path = require('node:path');
const { request } = require('@playwright/test');

module.exports = async () => {
  const dataDirectory = process.env.PHOTO_EDITOR_E2E_DATA_DIR;
  const databasePath = process.env.PHOTO_EDITOR_E2E_DB_PATH;
  if (!dataDirectory || !databasePath) {
    throw new Error('Photo editor E2E data paths were not configured');
  }
  fs.mkdirSync(dataDirectory, { recursive: true });
  if (process.env.PHOTO_EDITOR_E2E_AUTH !== '0') {
    const port = Number(process.env.PHOTO_EDITOR_E2E_PORT || 7013);
    const client = await request.newContext({ baseURL: `http://127.0.0.1:${port}` });
    try {
      const credentials = { username: 'editor-e2e', password: 'disposable-editor-e2e-password' };
      const setup = await client.post('/api/auth/setup', { data: credentials });
      if (!setup.ok()) throw new Error(`Disposable editor account setup failed: ${setup.status()}`);
      const login = await client.post('/api/auth/login', { data: credentials });
      if (!login.ok()) throw new Error(`Disposable editor account login failed: ${login.status()}`);
      await client.storageState({ path: path.join(dataDirectory, 'browser-auth.json') });
    } finally {
      await client.dispose();
    }
  }
};
