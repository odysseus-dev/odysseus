import { registerEscapeLayer } from './escMenuStack.js';

// Give every tutorial hint the same button, timeout, and Escape lifecycle.
// The global Escape arbiter closes one layer before its underlying window.
export function bindTourHintDismiss(hint, { timeout, fade = 280, onDismiss } = {}) {
  let closed = false;
  let timer;
  const button = hint.querySelector('.tour-hint-dismiss');
  const unregister = registerEscapeLayer(close, () => hint.isConnected && !closed);

  function close() {
    if (closed) return;
    closed = true;
    clearTimeout(timer);
    unregister();
    button?.removeEventListener('click', close);
    hint.classList.add('tour-hint-out');
    setTimeout(() => hint.remove(), fade);
    onDismiss?.();
  }

  button?.addEventListener('click', close);
  if (timeout > 0) timer = setTimeout(close, timeout);
  hint._dismiss = close;
  return close;
}
