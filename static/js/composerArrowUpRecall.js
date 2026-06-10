/**
 * ArrowUp/ArrowDown history traversal on the chat composer.
 * - ArrowUp on an empty composer recalls the most recent user prompt.
 * - Repeated ArrowUp walks further back through prior user prompts.
 * - ArrowDown walks forward again toward the empty/newest composer state.
 * - Non-empty composer is never hijacked (unless already in history traversal
 *   AND the value hasn't been edited since the last history navigation).
 * - If the user edits a recalled message, the traversal resets so ArrowUp/Down
 *   starts fresh from that edited content as a new draft.
 * - Scoped to #chat-history (active session only).
 */

/**
 * All user bubbles in the active chat surface (#chat-history), oldest→newest,
 * using dataset.raw (same source as resend/regenerate in chat.js).
 *
 * @param {Document | Element} [root=document]
 * @returns {string[]}
 */
export function getUserMessagesFromChatHistory(root = document) {
  const chatBox =
    root && root.id === 'chat-history' && typeof root.querySelectorAll === 'function'
      ? root
      : (root.getElementById ? root.getElementById('chat-history') : null);
  if (!chatBox) return [];

  const users = chatBox.querySelectorAll('.msg-user');
  return Array.from(users).map((el) => {
    const bodyEl = el.querySelector('.body');
    return el.dataset?.raw || (bodyEl ? bodyEl.textContent : '') || '';
  }).filter(Boolean);
}

/**
 * Kept for backwards compatibility with existing call sites.
 *
 * @param {Document | Element} [root=document]
 * @returns {string}
 */
export function getLastUserMessageFromChatHistory(root = document) {
  const msgs = getUserMessagesFromChatHistory(root);
  return msgs[msgs.length - 1] ?? '';
}

/**
 * @param {HTMLTextAreaElement} composer
 * @param {() => string[]} getUserMessages  returns oldest→newest array
 * @param {{ autoResize?: (el: HTMLTextAreaElement) => void }} [options]
 * @returns {boolean} true when wired (or already wired)
 */
export function wireArrowUpRecall(composer, getUserMessages, options = {}) {
  if (!composer) return false;
  if (composer._arrowUpRecallWired) return true;
  composer._arrowUpRecallWired = true;

  const { autoResize } = options;

  // -1 = live composer (not in history).
  // 0  = most recent message, 1 = one before that, etc.
  let historyIndex = -1;
  let savedDraft = '';
  // The value we last set via history navigation — used to detect user edits.
  let lastHistoryValue = null;

  function setValue(val) {
    composer.value = val;
    lastHistoryValue = val;
    try {
      composer.selectionStart = composer.selectionEnd = val.length;
    } catch (_) {}
    if (autoResize) autoResize(composer);
  }

  function isEditedSinceLastNav() {
    // If we're in history mode but the composer content no longer matches what
    // we set, the user has edited the recalled text → treat as new draft.
    return historyIndex !== -1 && composer.value !== lastHistoryValue;
  }

  composer.addEventListener('keydown', (e) => {
    // No modifier keys, no IME composition
    if (e.shiftKey || e.altKey || e.ctrlKey || e.metaKey) return;
    if (e.isComposing) return;

    const messages = getUserMessages(); // oldest→newest
    const total = messages.length;

    if (e.key === 'ArrowUp') {
      // If the user edited a recalled message, reset to treat it as a fresh draft.
      if (isEditedSinceLastNav()) {
        savedDraft = composer.value;
        historyIndex = -1;
        lastHistoryValue = null;
      }

      // Never hijack a non-empty composer unless already in history
      if (historyIndex === -1 && composer.value !== '') return;

      if (total === 0) return;

      if (historyIndex === -1) {
        // Entering history — save current draft (will be '' since we checked above)
        savedDraft = composer.value;
      }

      if (historyIndex < total - 1) {
        historyIndex++;
        e.preventDefault();
        // historyIndex 0 = newest, total-1 = oldest
        setValue(messages[total - 1 - historyIndex]);
      }
      return;
    }

    if (e.key === 'ArrowDown') {
      if (historyIndex === -1) return; // not in history, nothing to do

      // If the user edited a recalled message, reset — ArrowDown exits cleanly.
      if (isEditedSinceLastNav()) {
        savedDraft = composer.value;
        historyIndex = -1;
        lastHistoryValue = null;
        return; // don't preventDefault; let the cursor move naturally
      }

      e.preventDefault();
      historyIndex--;

      if (historyIndex === -1) {
        lastHistoryValue = null;
        setValue(savedDraft);
      } else {
        setValue(messages[total - 1 - historyIndex]);
      }
      return;
    }
  });

  // Reset history state when the user sends a message
  composer.addEventListener('send', () => {
    historyIndex = -1;
    savedDraft = '';
    lastHistoryValue = null;
  });

  return true;
}