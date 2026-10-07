const fs = require('node:fs');

module.exports = async () => {
  const dataDirectory = process.env.PHOTO_EDITOR_E2E_DATA_DIR;
  const databasePath = process.env.PHOTO_EDITOR_E2E_DB_PATH;
  if (!dataDirectory || !databasePath) {
    throw new Error('Photo editor E2E data paths were not configured');
  }
  fs.mkdirSync(dataDirectory, { recursive: true });
};
