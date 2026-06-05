import type { Connection } from './connection';
import { openSSE } from './sse';

// Typed client for the PC's /api/companion/* endpoints. Every call carries the
// pairing token as a Bearer header; the server resolves it to the token's real
// owner and scopes everything to that user (see companion/routes.py).

export interface PingResult {
  ok: boolean;
  name: string;
  version: string;
  auth: 'token' | 'session';
}

export interface SessionRow {
  id: string;
  name: string;
  model: string;
  message_count: number;
  is_important: boolean;
  active: boolean;
  status: string | null;
}

/** One decoded stream event. Text arrives as `{delta}`; everything else is a
 *  typed control/status event (`{type: 'tool_start' | 'model_info' | ...}`). */
export type StreamEvent =
  | { kind: 'delta'; text: string; thinking: boolean }
  | { kind: 'event'; type: string; raw: Record<string, unknown> }
  | { kind: 'done' };

function authHeaders(conn: Connection): Record<string, string> {
  return { Authorization: `Bearer ${conn.token}` };
}

async function getJSON<T>(conn: Connection, path: string): Promise<T> {
  const resp = await fetch(conn.baseUrl + path, { headers: authHeaders(conn) });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} on ${path}`);
  return (await resp.json()) as T;
}

export function ping(conn: Connection): Promise<PingResult> {
  return getJSON<PingResult>(conn, '/api/companion/ping');
}

export async function listSessions(conn: Connection): Promise<SessionRow[]> {
  const { sessions } = await getJSON<{ sessions: SessionRow[] }>(
    conn,
    '/api/companion/sessions',
  );
  return sessions;
}

export async function stopSession(conn: Connection, id: string): Promise<boolean> {
  const resp = await fetch(
    `${conn.baseUrl}/api/companion/sessions/${encodeURIComponent(id)}/stop`,
    { method: 'POST', headers: authHeaders(conn) },
  );
  if (!resp.ok) throw new Error(`HTTP ${resp.status} stopping session`);
  const { stopped } = (await resp.json()) as { stopped: boolean };
  return stopped;
}

/** Open the live SSE for a session and dispatch decoded events. Returns nothing;
 *  cancel by aborting the supplied signal. */
export function streamSession(
  conn: Connection,
  id: string,
  onEvent: (ev: StreamEvent) => void,
  opts: { signal?: AbortSignal; onError?: (e: unknown) => void; onClose?: () => void } = {},
): void {
  const url = `${conn.baseUrl}/api/companion/sessions/${encodeURIComponent(id)}/stream`;
  void openSSE(url, {
    headers: authHeaders(conn),
    signal: opts.signal,
    onError: opts.onError,
    onClose: opts.onClose,
    onData: (data) => {
      if (data === '[DONE]') {
        onEvent({ kind: 'done' });
        return;
      }
      let obj: Record<string, unknown>;
      try {
        obj = JSON.parse(data);
      } catch {
        // Non-JSON payloads are rare; surface them as raw text so nothing is lost.
        onEvent({ kind: 'delta', text: data, thinking: false });
        return;
      }
      if (typeof obj.delta === 'string') {
        // Odysseus flags reasoning tokens with thinking:true (src/llm_core.py).
        onEvent({ kind: 'delta', text: obj.delta, thinking: obj.thinking === true });
      } else if (typeof obj.type === 'string') {
        onEvent({ kind: 'event', type: obj.type, raw: obj });
      }
    },
  });
}
