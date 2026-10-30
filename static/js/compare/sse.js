function streamError(payload) {
  const detail = payload?.data ?? payload;
  const candidates = [detail?.message, detail?.text, detail?.error?.message,
    detail?.error, payload?.message, payload?.text, payload?.error?.message,
    payload?.error, typeof payload === 'string' ? payload : null];
  const message = candidates.find(value => typeof value === 'string' && value.trim());
  return new Error(message || 'Model request failed.');
}

/** Decode complete SSE frames, requiring the backend's terminal DONE event. */
export async function consumeCompareEvents(response, onEvent, onActivity) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let line = '', event = 'message', data = [], skipLF = false;
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (!done) onActivity?.();
      let chunk = decoder.decode(value, { stream: !done });
      if (skipLF && chunk.length) {
        if (chunk.startsWith('\n')) chunk = chunk.slice(1);
        skipLF = false;
      }
      const separators = /\r\n|\r|\n/g;
      let start = 0, separator;
      while ((separator = separators.exec(chunk))) {
        line += chunk.slice(start, separator.index);
        start = separator.index + separator[0].length;
        skipLF = separator[0] === '\r' && start === chunk.length;
        if (!line) {
          if (data.length || event === 'error') {
            const raw = data.join('\n');
            if (event !== 'error' && raw === '[DONE]') return;
            let payload;
            try { payload = JSON.parse(raw); }
            catch (_) {
              if (event === 'error') throw streamError(raw);
              throw new Error('Invalid model stream data.');
            }
            if (event === 'error' || payload?.type === 'error') throw streamError(payload);
            if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
              throw new Error('Invalid model stream data.');
            }
            await onEvent(payload);
          }
          event = 'message';
          data = [];
        } else if (!line.startsWith(':')) {
          const colon = line.indexOf(':');
          const field = colon < 0 ? line : line.slice(0, colon);
          let value = colon < 0 ? '' : line.slice(colon + 1);
          if (value.startsWith(' ')) value = value.slice(1);
          if (field === 'data') data.push(value);
          else if (field === 'event') event = value || 'message';
        }
        line = '';
      }
      line += chunk.slice(start);
      if (done) throw new Error('Stream ended before completion.');
    }
  } finally {
    try { await reader.cancel(); } catch (_) {}
    reader.releaseLock();
  }
}
