// Mock the browser-owned host facade for the Node email workflow harness.
// The mock looks up test globals at call time so each beforeEach setup remains
// isolated while production modules keep explicit host imports.
const lookup = (name) => new Proxy({}, {
  get(_target, property) {
    const value = globalThis[name]?.[property];
    return typeof value === 'function' ? value.bind(globalThis[name]) : value;
  },
});

export const uiModule = lookup('uiModule');
export const spinnerModule = lookup('spinnerModule');
export const sessionModule = lookup('sessionModule');
export const Modals = lookup('Modals');
export const emojiPicker = lookup('emojiPicker');
export const markdownModule = lookup('markdownModule');
export const codeRunnerModule = lookup('codeRunnerModule');
export const signatureModule = lookup('signatureModule');
export const documentLibrary = lookup('documentLibrary');

export const langIcon = (...args) => globalThis.langIcon?.(...args);
export const topPortalZ = (...args) => globalThis.topPortalZ?.(...args) ?? 1;
export const getDocumentStats = (...args) => globalThis.getDocumentStats?.(...args) ?? {};
export const parseMarkdownOutline = (...args) => globalThis.parseMarkdownOutline?.(...args) ?? [];
export const attachColorPicker = (...args) => globalThis.attachColorPicker?.(...args);
export const bindMenuDismiss = (...args) => globalThis.bindMenuDismiss?.(...args) ?? (() => {});
export const dismissOrRemove = (...args) => globalThis.dismissOrRemove?.(...args);
export const dismissTopMenu = (...args) => globalThis.dismissTopMenu?.(...args);
export const openLibrary = (...args) => globalThis.openLibrary?.(...args);
export const initLibrary = (...args) => globalThis.initLibrary?.(...args);

export const readEmailReplyResponse = (...args) =>
  globalThis.readEmailReplyResponse?.(...args) ?? Promise.resolve({ success: false, error: 'No email reply reader configured' });
export const closeLibrary = (...args) => globalThis.closeLibrary?.(...args);
export const isLibraryOpen = (...args) => globalThis.isLibraryOpen?.(...args) ?? false;
export const _closeVersionPanel = (...args) => globalThis._closeVersionPanel?.(...args);
export const _syncEditorPlaceholder = (...args) => globalThis._syncEditorPlaceholder?.(...args);
export const _activeDocLanguage = (...args) => globalThis._activeDocLanguage?.(...args) ?? '';
export const _scheduleDocumentStats = (...args) => globalThis._scheduleDocumentStats?.(...args);
export const _setDocumentHistoryControlState = (...args) => globalThis._setDocumentHistoryControlState?.(...args);
export const _wireSwipeDismiss = (...args) => globalThis._wireSwipeDismiss?.(...args);
export const toggleFullscreen = (...args) => globalThis.toggleFullscreen?.(...args);
export const toggleVersionHistory = (...args) => globalThis.toggleVersionHistory?.(...args);
export const loadEmailLibrary = (...args) => (
  typeof globalThis.loadEmailLibrary === 'function'
    ? globalThis.loadEmailLibrary(...args)
    : Promise.resolve({})
);
