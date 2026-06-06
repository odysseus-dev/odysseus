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

export interface ModelOption {
  endpointId: string;
  endpointName: string;
  model: string;
}

/** Flatten the caller's endpoints into pickable (endpoint, model) options. */
export async function listModels(conn: Connection): Promise<ModelOption[]> {
  const { endpoints } = await getJSON<{
    endpoints: { endpoint_id: string; name: string; models: string[] }[];
  }>(conn, '/api/companion/models');
  const out: ModelOption[] = [];
  for (const ep of endpoints) {
    for (const model of ep.models) {
      out.push({ endpointId: ep.endpoint_id, endpointName: ep.name, model });
    }
  }
  return out;
}

/** Per-turn capability toggles, mirroring the desktop: agent mode unlocks
 *  tools, web adds search, terminal allows the shell (and implies agent). */
export interface ChatOptions {
  agent: boolean;
  web: boolean;
  terminal: boolean;
}

export const DEFAULT_OPTIONS: ChatOptions = { agent: false, web: false, terminal: false };

export interface Attachment {
  id: string;
  name: string;
  mime: string;
  size: number;
  width?: number | null;
  height?: number | null;
}

/** Upload files from the phone (camera/gallery). Returns attachment ids to pass
 *  as `attachments` on a chat send. The ids are owned by the token's real owner,
 *  so the chat pipeline resolves them. */
export async function uploadFiles(conn: Connection, files: File[]): Promise<Attachment[]> {
  const fd = new FormData();
  for (const f of files) fd.append('files', f);
  // Note: do NOT set Content-Type -- the browser adds the multipart boundary.
  const resp = await fetch(`${conn.baseUrl}/api/companion/upload`, {
    method: 'POST',
    headers: authHeaders(conn),
    body: fd,
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} uploading`);
  const { files: out } = (await resp.json()) as { files: Attachment[] };
  return out;
}

/** Start a new chat. Returns the new session id to open + watch. */
export async function startSession(
  conn: Connection,
  opts: {
    message: string;
    endpointId: string;
    model: string;
    options?: ChatOptions;
    attachments?: string[];
  },
): Promise<{ session_id: string; name: string }> {
  const resp = await fetch(`${conn.baseUrl}/api/companion/sessions`, {
    method: 'POST',
    headers: { ...authHeaders(conn), 'Content-Type': 'application/json' },
    body: JSON.stringify({
      message: opts.message,
      endpoint_id: opts.endpointId,
      model: opts.model,
      attachments: opts.attachments,
      ...opts.options,
    }),
  });
  if (!resp.ok) throw new Error(`HTTP ${resp.status} starting session`);
  return (await resp.json()) as { session_id: string; name: string };
}

// ------------------------------------------------------------------ //
// Tools (email / calendar / notes / tasks). These hit the companion's
// owner-impersonating proxies over the desktop's existing routes.
// ------------------------------------------------------------------ //
async function postJSON<T>(conn: Connection, path: string, body: unknown): Promise<T> {
  const resp = await fetch(conn.baseUrl + path, {
    method: 'POST',
    headers: { ...authHeaders(conn), 'Content-Type': 'application/json' },
    body: JSON.stringify(body ?? {}),
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      detail = ((await resp.json()) as { detail?: string }).detail || detail;
    } catch {
      /* keep status */
    }
    throw new Error(detail);
  }
  return (await resp.json().catch(() => ({}))) as T;
}

function qs(params: Record<string, string | number | undefined>): string {
  const p = new URLSearchParams();
  for (const [k, v] of Object.entries(params)) if (v !== undefined && v !== '') p.set(k, String(v));
  const s = p.toString();
  return s ? `?${s}` : '';
}

export interface EmailAccount {
  id: string;
  email?: string;
  address?: string;
  name?: string;
  is_default?: boolean;
}
export interface EmailItem {
  uid: string | number;
  subject: string;
  from_name?: string;
  from_address?: string;
  date?: string;
  date_display?: string;
  is_read?: boolean;
  has_attachments?: boolean;
}
export interface EmailRead {
  uid: string | number;
  subject?: string;
  from_name?: string;
  from_address?: string;
  to?: string;
  date?: string;
  date_display?: string;
  body_text?: string;
  body_html?: string;
  body?: string;
  message_id?: string;
  folder?: string;
  cached_summary?: string | null;
  cached_ai_reply?: string | null;
}

export async function listEmailAccounts(conn: Connection): Promise<EmailAccount[]> {
  const { accounts } = await getJSON<{ accounts: EmailAccount[] }>(conn, '/api/companion/email/accounts');
  return accounts || [];
}
export function listEmails(
  conn: Connection,
  opts: { folder?: string; limit?: number; filter?: string; accountId?: string } = {},
): Promise<{ emails: EmailItem[]; total?: number; error?: string }> {
  return getJSON(
    conn,
    '/api/companion/email/list' +
      qs({ folder: opts.folder, limit: opts.limit ?? 40, filter: opts.filter, account_id: opts.accountId }),
  );
}
export function readEmail(
  conn: Connection,
  uid: string | number,
  opts: { folder?: string; accountId?: string } = {},
): Promise<EmailRead> {
  return getJSON(
    conn,
    `/api/companion/email/read/${encodeURIComponent(String(uid))}` +
      qs({ folder: opts.folder, account_id: opts.accountId }),
  );
}
export function flagEmail(
  conn: Connection,
  uid: string | number,
  action: 'mark-read' | 'mark-unread' | 'archive',
  opts: { folder?: string; accountId?: string } = {},
): Promise<unknown> {
  return postJSON(
    conn,
    `/api/companion/email/${encodeURIComponent(String(uid))}/flag` +
      qs({ action, folder: opts.folder, account_id: opts.accountId }),
    {},
  );
}
export function sendEmail(conn: Connection, body: Record<string, unknown>): Promise<unknown> {
  return postJSON(conn, '/api/companion/email/send', body);
}
export function summarizeEmail(
  conn: Connection,
  body: Record<string, unknown>,
): Promise<{ success: boolean; summary?: string; error?: string }> {
  return postJSON(conn, '/api/companion/email/summarize', body);
}
export function aiReplyEmail(
  conn: Connection,
  body: Record<string, unknown>,
): Promise<{ success: boolean; reply?: string; error?: string }> {
  return postJSON(conn, '/api/companion/email/ai-reply', body);
}

export interface CalEvent {
  title?: string;
  summary?: string;
  dtstart?: string;
  dtend?: string;
  all_day?: boolean;
  location?: string;
}
export function listEvents(conn: Connection, startISO: string, endISO: string): Promise<{ events: CalEvent[] }> {
  return getJSON(conn, '/api/companion/calendar/events' + qs({ start: startISO, end: endISO }));
}

export interface Note {
  id: string;
  title?: string;
  content?: string;
  items?: { text: string; checked?: boolean }[] | null;
  note_type?: string;
  pinned?: boolean;
  label?: string;
  updated_at?: string;
}
export async function listNotes(conn: Connection): Promise<Note[]> {
  const { notes } = await getJSON<{ notes: Note[] }>(conn, '/api/companion/notes');
  return notes || [];
}
export function createNote(conn: Connection, body: { title?: string; content?: string }): Promise<unknown> {
  return postJSON(conn, '/api/companion/notes', body);
}

export interface TaskRow {
  id: string;
  name?: string;
  status?: string;
  task_type?: string;
  action?: string;
  schedule?: string | null;
  next_run?: string | null;
  last_run?: string | null;
  trigger_type?: string | null;
}
export async function listTasks(conn: Connection): Promise<TaskRow[]> {
  const { tasks } = await getJSON<{ tasks: TaskRow[] }>(conn, '/api/companion/tasks');
  return tasks || [];
}
export function taskAction(conn: Connection, id: string, action: 'pause' | 'resume' | 'run'): Promise<unknown> {
  return postJSON(conn, `/api/companion/tasks/${encodeURIComponent(id)}/${action}`, {});
}

export interface SearchHit {
  session_id: string;
  session_name: string;
  role: string;
  content_snippet: string;
  timestamp: string | null;
}
/** Search the owner's chat history. Returns most-recent matches first. */
export function searchChats(conn: Connection, q: string, limit = 25): Promise<SearchHit[]> {
  return getJSON(conn, '/api/companion/search' + qs({ q, limit }));
}

export interface FsDir {
  name: string;
  path: string;
}
export interface FsFile {
  name: string;
  path: string;
  size: number;
}
export interface FsListing {
  path: string;
  parent: string | null;
  dirs: FsDir[];
  files: FsFile[];
}

/** Browse the PC filesystem (admin-only on the server). Empty path => home. */
export async function fsBrowse(conn: Connection, path = ''): Promise<FsListing> {
  return getJSON<FsListing>(
    conn,
    `/api/companion/fs/browse?path=${encodeURIComponent(path)}`,
  );
}

/** Copy a PC file into an attachment and return its metadata (incl. id). */
export async function fsAttach(conn: Connection, path: string): Promise<Attachment> {
  const resp = await fetch(`${conn.baseUrl}/api/companion/fs/attach`, {
    method: 'POST',
    headers: { ...authHeaders(conn), 'Content-Type': 'application/json' },
    body: JSON.stringify({ path }),
  });
  if (!resp.ok) {
    let detail = `HTTP ${resp.status}`;
    try {
      detail = ((await resp.json()) as { detail?: string }).detail || detail;
    } catch {
      /* keep status */
    }
    throw new Error(detail);
  }
  return (await resp.json()) as Attachment;
}

export interface MsgAttachment {
  id: string;
  name: string;
  mime: string;
}

export interface ChatMsg {
  role: 'user' | 'assistant';
  content: string;
  attachments?: MsgAttachment[];
}

/** URL for an attachment's bytes. Needs the bearer header, so callers fetch it
 *  rather than dropping it straight into an <img src> (see AuthImage). */
export function attachmentUrl(conn: Connection, id: string, thumb = true): string {
  return `${conn.baseUrl}/api/companion/upload/${encodeURIComponent(id)}${thumb ? '?thumb=1' : ''}`;
}

export interface SessionHistory {
  messages: ChatMsg[];
  /** The session's current model, so the in-chat picker can default to it. */
  model: string;
  name: string;
}

/** Saved conversation for a session (works for finished sessions too). */
export async function getMessages(conn: Connection, id: string): Promise<SessionHistory> {
  const data = await getJSON<{ messages: ChatMsg[]; model?: string; name?: string }>(
    conn,
    `/api/companion/sessions/${encodeURIComponent(id)}/messages`,
  );
  return { messages: data.messages, model: data.model || '', name: data.name || '' };
}

/** Send a follow-up turn into an existing session. Optionally switches the
 *  model first (the in-chat picker). The run streams via streamSession. */
export async function sendMessage(
  conn: Connection,
  id: string,
  opts: {
    message: string;
    endpointId?: string;
    model?: string;
    options?: ChatOptions;
    attachments?: string[];
  },
): Promise<void> {
  const resp = await fetch(
    `${conn.baseUrl}/api/companion/sessions/${encodeURIComponent(id)}/message`,
    {
      method: 'POST',
      headers: { ...authHeaders(conn), 'Content-Type': 'application/json' },
      body: JSON.stringify({
        message: opts.message,
        endpoint_id: opts.endpointId,
        model: opts.model,
        attachments: opts.attachments,
        ...opts.options,
      }),
    },
  );
  if (!resp.ok) throw new Error(`HTTP ${resp.status} sending message`);
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
