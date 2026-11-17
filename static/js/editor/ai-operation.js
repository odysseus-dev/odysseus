// Capture repeat clicks before tool handlers can start another request.
export function beginAIOperation(button, onCancel) {
  const controller = new AbortController();
  const title = button.title;
  button.disabled = false;
  button.title = 'Cancel running action';
  button.setAttribute('aria-busy', 'true');
  const cancel = event => {
    event.preventDefault();
    event.stopImmediatePropagation();
    if (!controller.signal.aborted) {
      controller.abort();
      onCancel?.();
    }
  };
  button.addEventListener('click', cancel, true);
  return {
    signal: controller.signal,
    finish() {
      button.removeEventListener('click', cancel, true);
      button.title = title;
      button.removeAttribute('aria-busy');
    },
  };
}

export function decodeAIImage(base64, signal) {
  return new Promise((resolve, reject) => {
    const image = new Image();
    const cleanup = () => {
      image.onload = image.onerror = null;
      signal.removeEventListener('abort', abort);
    };
    const abort = () => {
      cleanup();
      image.src = '';
      reject(new DOMException('Cancelled', 'AbortError'));
    };
    if (signal.aborted) { abort(); return; }
    signal.addEventListener('abort', abort, { once: true });
    image.onload = () => { cleanup(); resolve(image); };
    image.onerror = () => { cleanup(); reject(new Error('Failed to decode result image')); };
    image.src = base64.startsWith('data:') ? base64 : 'data:image/png;base64,' + base64;
  });
}
