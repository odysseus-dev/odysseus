// static/js/emailLibrary.js — compatibility wrapper.
//
// The email library now lives in `static/js/emailLibrary/`. This file stays at
// the old path because five call sites import it, four of them dynamically
// with a version query string (`emailInbox.js`, `chatStream.js`,
// `chatRenderer.js`, `document.js`, `settings.js`), and `sw.js` caches URLs
// verbatim. Re-exporting here means the move needed no coordinated edit to any
// of them.
//
// Importing this module evaluates `emailLibrary/index.js`, so the side effects
// the panel relies on — the `window.__odysseusGetActiveEmailContext` bridge,
// the agent tool-output listeners — still happen exactly when they used to.
//
// New code should import `./emailLibrary/index.js` directly.

export {
  refreshEmailLibrary,
  prewarmEmailLibrary,
  prewarmUnreadEmails,
  initEmailLibrary,
  isOpen,
  openEmailLibrary,
  openEmailFromTool,
  mountEmailSettings,
  openEmailLibrarySettings,
  closeEmailLibrary,
} from './emailLibrary/index.js';
