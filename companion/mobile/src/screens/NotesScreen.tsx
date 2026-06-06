import { useCallback, useEffect, useState } from 'react';
import type { Connection } from '../lib/connection';
import type { Note } from '../lib/api';
import { createNote, listNotes } from '../lib/api';
import { ChevronLeftIcon, PlusIcon } from '../components/icons';

// Notes: list the owner's notes/checklists, with a quick add (title + body).
export default function NotesScreen({ conn, onBack }: { conn: Connection; onBack: () => void }) {
  const [notes, setNotes] = useState<Note[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [adding, setAdding] = useState(false);
  const [title, setTitle] = useState('');
  const [content, setContent] = useState('');
  const [saving, setSaving] = useState(false);

  const refresh = useCallback(() => {
    setLoading(true);
    listNotes(conn)
      .then(setNotes)
      .catch(() => setError('Could not load your notes.'))
      .finally(() => setLoading(false));
  }, [conn]);

  useEffect(refresh, [refresh]);

  async function save() {
    if (!title.trim() && !content.trim()) return;
    setSaving(true);
    try {
      await createNote(conn, { title: title.trim(), content: content.trim() });
      setTitle('');
      setContent('');
      setAdding(false);
      refresh();
    } catch {
      setError('Could not save the note.');
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="screen detail">
      <header className="detail-header">
        <button className="ghost" onClick={onBack} type="button" aria-label="Back">
          <ChevronLeftIcon size={24} />
        </button>
        <span className="status-pill">Notes</span>
        <button className="ghost" onClick={() => setAdding((a) => !a)} type="button" aria-label="New note">
          <PlusIcon size={22} />
        </button>
      </header>

      {adding && (
        <div className="compose-body" style={{ flex: 'none' }}>
          <label className="field">
            <span>Title</span>
            <input
              className="note-input"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              placeholder="Note title"
            />
          </label>
          <label className="field">
            <span>Note</span>
            <textarea value={content} onChange={(e) => setContent(e.target.value)} placeholder="Write..." />
          </label>
          <button className="stop send" onClick={save} type="button" disabled={saving}>
            {saving ? 'Saving...' : 'Save note'}
          </button>
        </div>
      )}

      <div className="detail-body">
        {error && <div className="error">{error}</div>}
        {loading && <div className="muted pad">Loading...</div>}
        {!loading && !error && notes.length === 0 && <div className="muted pad">No notes yet.</div>}
        {notes.map((n) => (
          <div key={n.id} className="note-card">
            <div className="note-title">
              {n.pinned ? '* ' : ''}
              {n.title || '(untitled)'}
            </div>
            {n.items && n.items.length > 0 ? (
              <ul className="note-items">
                {n.items.map((it, i) => (
                  <li key={i} className={it.checked ? 'done' : ''}>
                    {it.checked ? '[x] ' : '[ ] '}
                    {it.text}
                  </li>
                ))}
              </ul>
            ) : (
              n.content && <p className="note-body">{n.content}</p>
            )}
          </div>
        ))}
      </div>
    </div>
  );
}
