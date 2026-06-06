import { useCallback, useEffect, useState } from 'react';
import type { Connection } from '../lib/connection';
import type { Attachment, FsListing } from '../lib/api';
import { fsAttach, fsBrowse } from '../lib/api';
import { ChevronLeftIcon, FileIcon, FolderIcon, XIcon } from './icons';

// Full-screen overlay that browses the PC filesystem (admin-only on the server)
// so the user can pick a file to attach. Tapping a folder navigates; tapping a
// file copies it into an attachment and hands it back via onPick.
export default function FsBrowser({
  conn,
  onClose,
  onPick,
}: {
  conn: Connection;
  onClose: () => void;
  onPick: (att: Attachment) => void;
}) {
  const [listing, setListing] = useState<FsListing | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [attaching, setAttaching] = useState<string | null>(null);

  const go = useCallback(
    async (path: string) => {
      setLoading(true);
      setError(null);
      try {
        setListing(await fsBrowse(conn, path));
      } catch (e) {
        setError(
          e instanceof Error && /403/.test(e.message)
            ? 'File browsing is admin-only on this server.'
            : 'Could not read that folder.',
        );
        console.warn('fsBrowse failed', e);
      } finally {
        setLoading(false);
      }
    },
    [conn],
  );

  useEffect(() => {
    go('');
  }, [go]);

  async function pickFile(path: string) {
    setAttaching(path);
    setError(null);
    try {
      onPick(await fsAttach(conn, path));
      onClose();
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not attach that file.');
      console.warn('fsAttach failed', e);
      setAttaching(null);
    }
  }

  return (
    <div className="screen detail fs">
      <header className="detail-header">
        <button className="ghost" onClick={onClose} type="button" aria-label="Cancel">
          <XIcon size={24} />
        </button>
        <span className="fs-path">{listing?.path || 'PC files'}</span>
      </header>

      {error && <div className="error">{error}</div>}

      <ul className="rows fs-rows">
        {listing?.parent && (
          <li>
            <button className="row" onClick={() => go(listing.parent as string)} type="button">
              <span className="chev">
                <ChevronLeftIcon size={20} />
              </span>
              <span className="row-main">
                <span className="row-title">..</span>
              </span>
            </button>
          </li>
        )}

        {listing?.dirs.map((d) => (
          <li key={d.path}>
            <button className="row" onClick={() => go(d.path)} type="button">
              <span className="fs-icon">
                <FolderIcon size={20} />
              </span>
              <span className="row-main">
                <span className="row-title">{d.name}</span>
              </span>
            </button>
          </li>
        ))}

        {listing?.files.map((f) => (
          <li key={f.path}>
            <button
              className="row"
              onClick={() => pickFile(f.path)}
              type="button"
              disabled={attaching !== null}
            >
              <span className="fs-icon">
                <FileIcon size={20} />
              </span>
              <span className="row-main">
                <span className="row-title">{f.name}</span>
                <span className="row-sub">
                  <span>{attaching === f.path ? 'Attaching...' : formatSize(f.size)}</span>
                </span>
              </span>
            </button>
          </li>
        ))}
      </ul>

      {loading && <div className="muted pad">Loading...</div>}
      {!loading && listing && listing.dirs.length === 0 && listing.files.length === 0 && (
        <div className="muted pad">Empty folder.</div>
      )}
    </div>
  );
}

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(0)} KB`;
  return `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
