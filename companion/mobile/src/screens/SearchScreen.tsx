import { useEffect, useRef, useState } from 'react';
import type { Connection } from '../lib/connection';
import type { SearchHit } from '../lib/api';
import { searchChats } from '../lib/api';
import { ChevronLeftIcon, SearchIcon } from '../components/icons';

// Search across the owner's chat history. Debounced; tapping a hit opens that
// session.
export default function SearchScreen({
  conn,
  onBack,
  onOpen,
}: {
  conn: Connection;
  onBack: () => void;
  onOpen: (sessionId: string) => void;
}) {
  const [q, setQ] = useState('');
  const [hits, setHits] = useState<SearchHit[]>([]);
  const [loading, setLoading] = useState(false);
  const [searched, setSearched] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    inputRef.current?.focus();
  }, []);

  useEffect(() => {
    const term = q.trim();
    if (!term) {
      setHits([]);
      setSearched(false);
      setLoading(false);
      return;
    }
    setLoading(true);
    const t = setTimeout(() => {
      searchChats(conn, term)
        .then((r) => {
          setHits(r);
          setSearched(true);
        })
        .catch(() => setSearched(true))
        .finally(() => setLoading(false));
    }, 300);
    return () => clearTimeout(t);
  }, [q, conn]);

  return (
    <div className="screen detail">
      <header className="detail-header">
        <button className="ghost" onClick={onBack} type="button" aria-label="Back">
          <ChevronLeftIcon size={24} />
        </button>
        <div className="search-field">
          <SearchIcon size={16} />
          <input
            ref={inputRef}
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Search your chats"
            autoCapitalize="none"
            autoCorrect="off"
            inputMode="search"
          />
        </div>
      </header>
      <div className="detail-body">
        {loading && <div className="muted pad">Searching...</div>}
        {!loading && searched && hits.length === 0 && <div className="muted pad">No matches.</div>}
        {!loading && !searched && (
          <div className="muted pad">Type to search your past conversations.</div>
        )}
        {hits.map((h, i) => (
          <button key={i} className="row search-hit" onClick={() => onOpen(h.session_id)} type="button">
            <span className="row-main">
              <span className="row-title">{h.session_name || 'Untitled'}</span>
              <span className="search-snippet">
                <span className="search-role">{h.role}</span>
                {h.content_snippet}
              </span>
            </span>
          </button>
        ))}
      </div>
    </div>
  );
}
