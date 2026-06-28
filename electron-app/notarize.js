// electron-builder afterSign hook: notarize the macOS build if credentials
// are present in the environment. No-op otherwise (unsigned/local builds).
//
// Set these env vars to enable notarization:
//   APPLE_ID            — your Apple ID email
//   APPLE_TEAM_ID       — your Developer team ID
//   APPLE_APP_SPECIFIC_PASSWORD — app-specific password for notarytool
//
// Adopts the same optional-notarization pattern as #3769's build.sh, but
// uses electron-notarize (bundled with electron-builder) instead of
// invoking xcrun notarytool directly.
const { notarize } = require('@electron/notarize');

exports.default = async function notarizeHook(context) {
  const { APPLE_ID, APPLE_TEAM_ID, APPLE_APP_SPECIFIC_PASSWORD } = process.env;
  if (!APPLE_ID || !APPLE_TEAM_ID || !APPLE_APP_SPECIFIC_PASSWORD) {
    console.log('[odysseus] Skipping notarization — APPLE_ID / APPLE_TEAM_ID / APPLE_APP_SPECIFIC_PASSWORD not set.');
    return;
  }
  if (context.electronPlatformName !== 'darwin') return;

  const appPath = context.appOutDir;
  console.log(`[odysseus] Notarizing ${appPath}…`);
  await notarize({
    appBundleId: 'com.odysseus.app',
    appPath,
    appleId: APPLE_ID,
    appleIdPassword: APPLE_APP_SPECIFIC_PASSWORD,
    teamId: APPLE_TEAM_ID,
  });
  console.log('[odysseus] Notarization complete.');
};