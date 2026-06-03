export function shouldRecallLastUserMessage(input, event) {
  if (!input || !event || event.key !== 'ArrowUp') return false;
  if (input.value !== '') return false;
  if (event.shiftKey || event.altKey || event.ctrlKey || event.metaKey) return false;
  if (event.isComposing !== false) return false;
  return true;
}

export function getLastUserMessageRaw(chatHistory) {
  const userMessages = chatHistory?.querySelectorAll?.('.msg-user');
  if (!userMessages || userMessages.length === 0) return '';
  const lastUserMessage = userMessages[userMessages.length - 1];
  const raw = lastUserMessage?.dataset?.raw;
  return typeof raw === 'string' ? raw : '';
}

function dispatchInputEvent(input) {
  if (typeof input?.dispatchEvent !== 'function') return;

  const EventCtor = input.ownerDocument?.defaultView?.Event || globalThis.Event;
  if (typeof EventCtor !== 'function') return;
  input.dispatchEvent(new EventCtor('input', { bubbles: true }));
}

export function recallLastUserMessageFromHistory(input, chatHistory, event) {
  if (!shouldRecallLastUserMessage(input, event)) return false;

  const raw = getLastUserMessageRaw(chatHistory);
  if (raw === '') return false;

  if (typeof event.preventDefault === 'function') event.preventDefault();
  input.value = raw;
  if (typeof input.setSelectionRange === 'function') {
    input.setSelectionRange(raw.length, raw.length);
  }
  dispatchInputEvent(input);
  return true;
}
