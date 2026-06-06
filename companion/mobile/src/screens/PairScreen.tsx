import { useState } from 'react';
import type { Connection } from '../lib/connection';
import { normalizeBaseUrl, parsePairingPayload, saveConnection } from '../lib/connection';
import { ping } from '../lib/api';
import { BrandMark, QrIcon } from '../components/icons';
import QrScanner from '../components/QrScanner';

// First-run screen: point the app at your Odysseus and pair. Scan the QR on the
// PC's  <server>/api/companion/pair  page (admin only), or enter the address and
// token by hand. The pairing payload is JSON {v, host, port, token}.
export default function PairScreen({ onPaired }: { onPaired: (c: Connection) => void }) {
  const [url, setUrl] = useState('');
  const [token, setToken] = useState('');
  const [busy, setBusy] = useState(false);
  const [scanning, setScanning] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function connectWith(conn: Connection) {
    setBusy(true);
    setError(null);
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

  function connect() {
    const baseUrl = normalizeBaseUrl(url);
    if (!baseUrl || !token.trim()) {
      setError('Enter your server address and a pairing token.');
      return;
    }
    connectWith({ baseUrl, token: token.trim() });
  }

  function onScan(text: string) {
    setScanning(false);
    const parsed = parsePairingPayload(text);
    if (!parsed) {
      setError('That QR code is not an Odysseus pairing code.');
      return;
    }
    // Prefill the fields too, so a failed ping leaves something to edit.
    setUrl(parsed.baseUrl);
    setToken(parsed.token);
    connectWith(parsed);
  }

  if (scanning) {
    return <QrScanner onResult={onScan} onClose={() => setScanning(false)} />;
  }

  return (
    <div className="screen pair">
      <div className="brand">
        <BrandMark size={30} />
        <span>Odysseus</span>
      </div>
      <p className="muted">Pair this phone with your PC to use it as a remote.</p>

      <button className="primary scan" onClick={() => setScanning(true)} type="button" disabled={busy}>
        <QrIcon size={20} />
        <span>Scan pairing code</span>
      </button>
      <div className="or-divider">
        <span>or enter manually</span>
      </div>

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
        Generate a pairing code on the PC at <code>/api/companion/pair</code> (admin).
        For access away from home, put your PC and phone on a private tunnel
        (e.g. Tailscale) and pair with that address.
      </p>
    </div>
  );
}
