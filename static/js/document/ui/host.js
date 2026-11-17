// frontend/src/features/documents/ui/host.js
/**
 * Legacy host bridge re-exporting dependencies with exact query strings.
 * Only UI layer modules may import this bridge; domain and application must not.
 */

export { default as uiModule } from '/static/js/ui.js?v=20260916largetoolscroll1';
export { default as sessionModule } from '/static/js/sessions.js';
export { default as emojiPicker } from '/static/js/emojiPicker.js';
export { readEmailReplyResponse } from '/static/js/emailReplyStream.js';
export { default as markdownModule } from '/static/js/markdown.js';
export { default as codeRunnerModule } from '/static/js/codeRunner.js?v=20260831richtexttools91';
export { langIcon } from '/static/js/langIcons.js?v=20260831richtexttools91';
export { default as spinnerModule } from '/static/js/spinner.js';
export {
  openLibrary,
  closeLibrary,
  isLibraryOpen,
  initLibrary,
} from '/static/js/documentLibrary.js?v=20260911librarybulkdelete1';
export * as documentLibrary from '/static/js/documentLibrary.js?v=20260911librarybulkdelete1';
export { default as signatureModule } from '/static/js/signature.js';
export * as Modals from '/static/js/modalManager.js';
export {
  bindMenuDismiss,
  dismissOrRemove,
  dismissTopMenu,
} from '/static/js/escMenuStack.js';
export { topPortalZ } from '/static/js/toolWindowZOrder.js';
export { getDocumentStats } from '/static/js/documentStats.js?v=20260831richtexttools91';
export { parseMarkdownOutline } from '/static/js/documentOutline.js?v=20260831richtexttools91';
export { attachColorPicker } from '/static/js/colorPicker.js?v=20260910eyedropper1';

export function loadEmailLibrary() {
  return import('/static/js/emailLibrary.js?v=20260915trashmove2');
}
