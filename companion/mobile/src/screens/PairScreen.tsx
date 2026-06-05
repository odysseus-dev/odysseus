import { useState } from 'react';
import type { Connection } from '../lib/connection';
import { normalizeBaseUrl, saveConnection } from '../lib/connection';
import { ping } from '../lib/api';
import { BrandMark } from '../components/icons';

// First-run screen: point the app at your Odysseus and paste the pairing token.
// Generate the token on the PC at  <server>/api/companion/pair  (admin only).
// A QR scanner is a planned follow-up -- the pairing payload is already JSON
// {v, host, port, token}, so scanning can prefill both fields.
export default function PairScreen({ onPaired }: { onPaired: (c: Connection) => void }) {
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function connect() {
    setError(null);
    const baseUrl = normalizeBaseUrl(url);
    if (!baseUrl || !token.trim()) {
      setError('Enter your server address and a pairing token.');
      return;
    }
    const conn: Connection = { baseUrl, token: token.trim() };
    setBusy(true);
    try {
      const res = await ping(conn); // validates host + token in one round-trip
      conn.name = res.name;
      await saveConnection(conn);
      onPaired(conn);
    } catch (e) {
      setError(
        'Could not reach the server with that token. Check the address, that ' +
          'the token is current, and that your phone can reach the PC.',
      );
      console.warn('pair failed', e);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="screen pair">
      <div className="brand">
        <BrandMark size={30} />
        <span>Odysseus</span>
      </div>
      <p className="muted">Pair this phone with your PC to use it as a remote.</p>

      <label className="field">
        <span>Server address</span>
        <input
          value={url}
          onChange={(e) => setUrl(e.target.value)}
          placeholder="192.168.1.50:7000"
          autoCapitalize="none"
          autoCorrect="off"
          inputMode="url"
        />
      </label>

      <label className="field">
        <span>Pairing token</span>
        <input
          value={token}
          onChange={(e) => setToken(e.target.value)}
          placeholder="ody_..."
          autoCapitalize="none"
          autoCorrect="off"
        />
      </label>

      {error && <div className="error">{error}</div>}

      <button className="primary" onClick={connect} disabled={busy} type="button">
        {busy ? 'Connecting...' : 'Connect'}
      </button>

      <p className="hint">
        Generate a token on the PC at <code>/api/companion/pair</code> (admin).
        For access away from home, put your PC and phone on a private tunnel
        (e.g. Tailscale) and pair with that address.
      </p>
    </div>
  );
}
