/**
 * ArrowUp on an empty composer recalls the last user message (chat-app convention).
 */

/**
 * Last user bubble in the active chat surface (#chat-history), using dataset.raw
 * (same source as resend/regenerate in chat.js).
 *
 * @param {Document | Element} [root=document]
 * @returns {string}
 */
export function getLastUserMessageFromChatHistory(root = document) {
  const chatBox =
    root && root.id === 'chat-history' && typeof root.querySelectorAll === 'function'
      ? root
      : (root.getElementById ? root.getElementById('chat-history') : null);
  if (!chatBox) return '';

  const users = chatBox.querySelectorAll('.msg-user');
  const last = users[users.length - 1];
  if (!last) return '';

  const bodyEl = last.querySelector('.body');
  return last.dataset?.raw || (bodyEl ? bodyEl.textContent : '') || '';
}

/**
 * @param {HTMLTextAreaElement} composer
 * @param {() => string} getLastUserMessage
 * @param {{ autoResize?: (el: HTMLTextAreaElement) => void }} [options]
 * @returns {boolean} true when wired (or already wired)
 */
export function wireArrowUpRecall(composer, getLastUserMessage, options = {}) {
  if (!composer) return false;
  if (composer._arrowUpRecallWired) return true;
  composer._arrowUpRecallWired = true;

  const { autoResize } = options;

  composer.addEventListener('keydown', (e) => {
    // Only ArrowUp, no modifier keys, no IME composition
    if (e.key !== 'ArrowUp') return;
    if (e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.isComposing) return;

    // Literal emptiness — intentional whitespace is not empty
    if (composer.value !== '') return;

    const recalled = getLastUserMessage();
    if (!recalled) return;

    e.preventDefault();
    composer.value = recalled;
    try {
      composer.selectionStart = composer.selectionEnd = recalled.length;
    } catch (_) {}
    if (autoResize) autoResize(composer);
  });

  return true;
}
