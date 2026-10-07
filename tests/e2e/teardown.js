const fs = require('node:fs');

module.exports = async () => {
  const dataDirectory = process.env.PHOTO_EDITOR_E2E_DATA_DIR;
  if (!dataDirectory) return;
  fs.rmSync(dataDirectory, { recursive: true, force: true });
};
