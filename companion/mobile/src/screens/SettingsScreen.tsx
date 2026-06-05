import { useEffect, useState } from 'react';
import type { Connection } from '../lib/connection';
import { clearConnection } from '../lib/connection';
import { ping, type PingResult } from '../lib/api';

// Connection status + unpair. Future home for the notifications (silent/loud)
// and remote-access toggles from the roadmap.
export default function SettingsScreen({
  conn,
  onDisconnect,
}: {
  conn: Connection;
  onDisconnect: () => void;
}) {
  const [info, setInfo] = useState<PingResult | null>(null);
  const [reachable, setReachable] = useState<boolean | null>(null);

  useEffect(() => {
    ping(conn)
      .then((r) => {
        setInfo(r);
        setReachable(true);
      })
      .catch(() => setReachable(false));
  }, [conn]);

  async function disconnect() {
    await clearConnection();
    onDisconnect();
  }

  return (
    <div className="screen list">
      <header className="list-header">
        <h1>Settings</h1>
      </header>

      <div className="card">
        <div className="card-row">
          <span className="muted">Server</span>
          <span>{conn.name || conn.baseUrl}</span>
        </div>
        <div className="card-row">
          <span className="muted">Address</span>
          <span className="mono">{conn.baseUrl}</span>
        </div>
        <div className="card-row">
          <span className="muted">Status</span>
          <span>
            {reachable === null
              ? 'Checking...'
              : reachable
                ? `Connected${info ? ` (v${info.version})` : ''}`
                : 'Unreachable'}
          </span>
        </div>
      </div>

      <button className="danger" onClick={disconnect} type="button">
        Unpair this device
      </button>
    </div>
  );
}
