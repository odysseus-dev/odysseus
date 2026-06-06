import { useCallback, useEffect, useState } from 'react';
import type { Connection } from '../lib/connection';
import type { SessionRow } from '../lib/api';
import { listSessions } from '../lib/api';
import { RefreshIcon, ChevronRightIcon, PlusIcon, SearchIcon } from '../components/icons';

// The home tab: the owner's sessions, live ones first. Polls lightly so a run
// that starts on the desktop shows up here without a manual refresh.
export default function SessionsScreen({
  conn,
  onOpen,
  onNew,
  onSearch,
}: {
  conn: Connection;
  onOpen: (id: string) => void;
  onNew: () => void;
  onSearch: () => void;
}) {
  const [sessions, setSessions] = useState<SessionRow[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);

  const refresh = useCallback(async () => {
    try {
      setSessions(await listSessions(conn));
      setError(null);
    } catch (e) {
      setError('Lost contact with the server.');
      console.warn('listSessions failed', e);
    } finally {
      setLoading(false);
    }
  }, [conn]);

  useEffect(() => {
    refresh();
    const t = setInterval(refresh, 5000);
    return () => clearInterval(t);
  }, [refresh]);

  return (
    <div className="screen list">
      <header className="list-header">
        <h1>Sessions</h1>
        <div className="header-actions">
          <button className="ghost" onClick={onSearch} type="button" aria-label="Search chats">
            <SearchIcon size={20} />
          </button>
          <button className="ghost" onClick={refresh} type="button" aria-label="Refresh">
            <RefreshIcon size={20} />
          </button>
          <button className="ghost" onClick={onNew} type="button" aria-label="New chat">
            <PlusIcon size={22} />
          </button>
        </div>
      </header>

      {error && <div className="error">{error}</div>}
      {loading && sessions.length === 0 && <div className="muted pad">Loading...</div>}
      {!loading && sessions.length === 0 && !error && (
        <div className="muted pad">No sessions yet. Start one on your PC.</div>
      )}

      <ul className="rows">
        {sessions.map((s) => (
          <li key={s.id}>
            <button className="row" onClick={() => onOpen(s.id)} type="button">
              <span className={'dot' + (s.active ? ' live' : '')} aria-hidden />
              <span className="row-main">
                <span className="row-title">{s.name || 'Untitled'}</span>
                <span className="row-sub">
                  <span>{s.model || 'unknown model'}</span>
                  <span className="sep" />
                  <span>{s.message_count} msg</span>
                  {s.active && (
                    <>
                      <span className="sep" />
                      <span>running</span>
                    </>
                  )}
                </span>
              </span>
              <span className="chev">
                <ChevronRightIcon size={20} />
              </span>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
