import { Preferences } from '@capacitor/preferences';

// A paired server: where it is + the chat-scoped token minted by the PC's
// `/api/companion/pair` screen. This is the ONLY state the app needs to act as a
// remote -- the phone stores no chat data of its own.
export interface Connection {
  /** Base URL, no trailing slash, e.g. "http://192.168.1.50:7000" or a tunnel. */
  baseUrl: string;
  /** The `ody_...` pairing token. Sent as `Authorization: Bearer`. */
  token: string;
  /** Optional friendly label + resolved owner, filled in after a successful ping. */
  name?: string;
  owner?: string;
}

const KEY = 'odysseus.connection';

export function normalizeBaseUrl(raw: string): string {
  let u = raw.trim();
  if (!u) return '';
  if (!/^https?:\/\//i.test(u)) u = 'http://' + u; // bare host/IP -> http
  return u.replace(/\/+$/, '');
}

// Decode a scanned pairing QR. The PC encodes compact JSON
// {v, host, port, token} (companion/pairing.py); turn it into a Connection.
// Returns null for anything that isn't a valid pairing payload.
export function parsePairingPayload(raw: string): Connection | null {
  try {
    const obj = JSON.parse(raw) as { host?: unknown; port?: unknown; token?: unknown };
    if (typeof obj.host !== 'string' || !obj.host || !obj.token) return null;
    const port = obj.port ? `:${obj.port}` : '';
    const baseUrl = normalizeBaseUrl(`${obj.host}${port}`);
    if (!baseUrl) return null;
    return { baseUrl, token: String(obj.token) };
  } catch {
    return null;
  }
}

export async function loadConnection(): Promise<Connection | null> {
  const { value } = await Preferences.get({ key: KEY });
  if (!value) return null;
  try {
    return JSON.parse(value) as Connection;
  } catch {
    return null;
  }
}

export async function saveConnection(conn: Connection): Promise<void> {
  await Preferences.set({ key: KEY, value: JSON.stringify(conn) });
}

export async function clearConnection(): Promise<void> {
  await Preferences.remove({ key: KEY });
}
