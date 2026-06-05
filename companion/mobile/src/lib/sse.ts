// Minimal SSE reader built on fetch, because the browser's native EventSource
// cannot send an Authorization header -- and our pairing token must ride as a
// Bearer header. We read the response body as a stream and split it into SSE
// frames ourselves (events are separated by a blank line; payload lines start
// with "data:"; lines starting with ":" are comments/heartbeats we ignore).

export interface SSEHandlers {
  /** One decoded `data:` payload (already stripped of the "data: " prefix). */
  onData: (data: string) => void;
  onError?: (err: unknown) => void;
  /** Stream ended cleanly (server closed or `[DONE]` was handled upstream). */
  onClose?: () => void;
}

export interface SSEOptions extends SSEHandlers {
  headers?: Record<string, string>;
  signal?: AbortSignal;
}

export async function openSSE(url: string, opts: SSEOptions): Promise<void> {
  try {
    const resp = await fetch(url, {
      method: 'GET',
      headers: { Accept: 'text/event-stream', ...(opts.headers || {}) },
      signal: opts.signal,
    });
    if (!resp.ok || !resp.body) {
      throw new Error(`stream failed: HTTP ${resp.status}`);
    }

    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buf = '';

    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });

      // Dispatch every complete frame (separated by a blank line). Handle both
      // \n\n and \r\n\r\n.
      let sep: number;
      while ((sep = indexOfFrameEnd(buf)) !== -1) {
        const frame = buf.slice(0, sep);
        buf = buf.slice(sep).replace(/^(\r?\n){2}/, '');
        const data = parseFrame(frame);
        if (data !== null) opts.onData(data);
      }
    }
    opts.onClose?.();
  } catch (err) {
    // An aborted fetch is an expected, quiet teardown -- not an error to surface.
    if ((err as { name?: string })?.name === 'AbortError') {
      opts.onClose?.();
      return;
    }
    opts.onError?.(err);
  }
}

function indexOfFrameEnd(s: string): number {
  const a = s.indexOf('\n\n');
  const b = s.indexOf('\r\n\r\n');
  if (a === -1) return b;
  if (b === -1) return a;
  return Math.min(a, b);
}

/** Pull the concatenated `data:` lines out of one SSE frame, or null if none. */
function parseFrame(frame: string): string | null {
  const lines = frame.split(/\r?\n/);
  const dataLines: string[] = [];
  for (const line of lines) {
    if (line.startsWith(':')) continue; // comment / heartbeat
    if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).replace(/^ /, ''));
    }
  }
  return dataLines.length ? dataLines.join('\n') : null;
}
