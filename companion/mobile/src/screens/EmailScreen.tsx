import { useCallback, useEffect, useState } from 'react';
import type { Connection } from '../lib/connection';
import type { EmailAccount, EmailItem, EmailRead } from '../lib/api';
import {
  flagEmail,
  listEmailAccounts,
  listEmails,
  readEmail,
  sendEmail,
} from '../lib/api';
import { ChevronLeftIcon, RefreshIcon, SendIcon } from '../components/icons';

type View = 'list' | 'read' | 'compose';

// Email: list the inbox, open a message, reply or compose, archive / mark read.
// All owner-scoped via the companion email proxies.
export default function EmailScreen({ conn, onBack }: { conn: Connection; onBack: () => void }) {
  const [accounts, setAccounts] = useState<EmailAccount[] | null>(null);
  const [accountId, setAccountId] = useState<string | undefined>(undefined);
  const [emails, setEmails] = useState<EmailItem[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(true);
  const [view, setView] = useState<View>('list');
  const [open, setOpen] = useState<EmailRead | null>(null);

  // compose state
  const [to, setTo] = useState('');
  const [subject, setSubject] = useState('');
  const [body, setBody] = useState('');
  const [sending, setSending] = useState(false);

  useEffect(() => {
    listEmailAccounts(conn)
      .then((a) => {
        setAccounts(a);
        if (a.length) setAccountId(a[0].id);
      })
      .catch(() => setError('Could not load email accounts.'));
  }, [conn]);

  const refresh = useCallback(() => {
    setLoading(true);
    setError(null);
    listEmails(conn, { accountId, limit: 40 })
      .then((r) => {
        if (r.error) setError(r.error);
        setEmails(r.emails || []);
      })
      .catch(() => setError('Could not load your inbox.'))
      .finally(() => setLoading(false));
  }, [conn, accountId]);

  useEffect(() => {
    if (accounts && accounts.length) refresh();
    else if (accounts) setLoading(false);
  }, [accounts, refresh]);

  async function openEmail(item: EmailItem) {
    setView('read');
    setOpen(null);
    try {
      const full = await readEmail(conn, item.uid, { accountId });
      setOpen(full);
      if (!item.is_read) flagEmail(conn, item.uid, 'mark-read', { accountId }).catch(() => {});
    } catch {
      setError('Could not open that email.');
      setView('list');
    }
  }

  async function archive(uid: string | number) {
    try {
      await flagEmail(conn, uid, 'archive', { accountId });
      setView('list');
      setEmails((es) => es.filter((e) => e.uid !== uid));
    } catch {
      setError('Could not archive.');
    }
  }

  function startReply(e: EmailRead) {
    setTo(e.from_address || '');
    setSubject(/^re:/i.test(e.subject || '') ? e.subject || '' : `Re: ${e.subject || ''}`);
    setBody('');
    setView('compose');
  }

  function startCompose() {
    setTo('');
    setSubject('');
    setBody('');
    setView('compose');
  }

  async function doSend() {
    if (!to.trim()) return;
    setSending(true);
    try {
      await sendEmail(conn, { to: to.trim(), subject: subject.trim(), body, account_id: accountId });
      setView('list');
    } catch (e) {
      setError(e instanceof Error ? e.message : 'Could not send.');
    } finally {
      setSending(false);
    }
  }

  // ---- compose view ----
  if (view === 'compose') {
    return (
      <div className="screen detail">
        <header className="detail-header">
          <button className="ghost" onClick={() => setView('list')} type="button" aria-label="Back">
            <ChevronLeftIcon size={24} />
          </button>
          <span className="status-pill">Compose</span>
          <button className="stop send" onClick={doSend} type="button" disabled={sending || !to.trim()}>
            {sending ? '...' : 'Send'}
          </button>
        </header>
        <div className="compose-body">
          <label className="field">
            <span>To</span>
            <input className="note-input" value={to} onChange={(e) => setTo(e.target.value)} autoCapitalize="none" placeholder="name@example.com" inputMode="email" />
          </label>
          <label className="field">
            <span>Subject</span>
            <input className="note-input" value={subject} onChange={(e) => setSubject(e.target.value)} placeholder="Subject" />
          </label>
          <label className="field grow">
            <span>Message</span>
            <textarea value={body} onChange={(e) => setBody(e.target.value)} placeholder="Write your message..." />
          </label>
          {error && <div className="error">{error}</div>}
        </div>
      </div>
    );
  }

  // ---- read view ----
  if (view === 'read') {
    const text = open ? open.body_text || stripHtml(open.body_html) || open.body || '' : '';
    return (
      <div className="screen detail">
        <header className="detail-header">
          <button className="ghost" onClick={() => setView('list')} type="button" aria-label="Back">
            <ChevronLeftIcon size={24} />
          </button>
          <span className="status-pill">Email</span>
          {open && (
            <button className="ghost" onClick={() => archive(open.uid)} type="button">
              Archive
            </button>
          )}
        </header>
        <div className="detail-body">
          {!open && <div className="muted pad">Loading...</div>}
          {open && (
            <>
              <h2 className="email-subject">{open.subject || '(no subject)'}</h2>
              <div className="email-from">{open.from_name || open.from_address}</div>
              <div className="email-date">{open.date_display || open.date}</div>
              <pre className="msg-text email-body">{text}</pre>
              <button className="stop send email-reply" onClick={() => startReply(open)} type="button">
                <SendIcon size={16} /> Reply
              </button>
            </>
          )}
        </div>
      </div>
    );
  }

  // ---- list view ----
  return (
    <div className="screen detail">
      <header className="detail-header">
        <button className="ghost" onClick={onBack} type="button" aria-label="Back">
          <ChevronLeftIcon size={24} />
        </button>
        <span className="status-pill">Inbox</span>
        <div className="header-actions">
          <button className="ghost" onClick={refresh} type="button" aria-label="Refresh">
            <RefreshIcon size={20} />
          </button>
          {accounts && accounts.length > 0 && (
            <button className="ghost" onClick={startCompose} type="button" aria-label="Compose">
              <SendIcon size={20} />
            </button>
          )}
        </div>
      </header>
      <div className="detail-body">
        {accounts && accounts.length === 0 && (
          <div className="muted pad">No email account yet. Add one on your PC under Settings &rarr; Email.</div>
        )}
        {error && <div className="error">{error}</div>}
        {loading && <div className="muted pad">Loading...</div>}
        {emails.map((e) => (
          <button key={String(e.uid)} className="row email-row" onClick={() => openEmail(e)} type="button">
            <span className={'dot' + (e.is_read ? '' : ' live')} aria-hidden />
            <span className="row-main">
              <span className="row-title">{e.from_name || e.from_address || 'Unknown'}</span>
              <span className="email-subj">{e.subject || '(no subject)'}</span>
            </span>
            <span className="email-when">{shortDate(e.date_display || e.date)}</span>
          </button>
        ))}
      </div>
    </div>
  );
}

function stripHtml(html?: string): string {
  if (!html) return '';
  const tmp = document.createElement('div');
  tmp.innerHTML = html;
  return (tmp.textContent || '').replace(/\n{3,}/g, '\n\n').trim();
}

function shortDate(d?: string): string {
  if (!d) return '';
  const dt = new Date(d);
  if (isNaN(dt.getTime())) return d.slice(0, 10);
  const today = new Date();
  if (dt.toDateString() === today.toDateString())
    return dt.toLocaleTimeString(undefined, { hour: 'numeric', minute: '2-digit' });
  return dt.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}
