import { useEffect, useMemo, useRef, useState } from 'react';
import type { Connection } from '../lib/connection';
import type { ChatMsg, StreamEvent } from '../lib/api';
import { getMessages, stopSession, streamSession } from '../lib/api';
import { splitThinking } from '../lib/thinking';
import { ChevronLeftIcon } from '../components/icons';

type Stream = 'loading' | 'connecting' | 'live' | 'done' | 'inactive' | 'error';

// Session view. Loads the saved conversation first (so ANY session opens, not
// just one with a live run), then attaches the live SSE on top: if a run is
// active its output streams into an in-progress assistant bubble; if not, we
// just show the saved messages. Reasoning models' chain-of-thought shows in a
// collapsible "Thinking" block while live (it's stripped from saved messages,
// same as the desktop).
export default function SessionScreen({
  conn,
  sessionId,
  onBack,
}: {
  conn: Connection;
  sessionId: string;
  onBack: () => void;
}) {
  const [messages, setMessages] = useState<ChatMsg[]>([]);
  const [rawAnswer, setRawAnswer] = useState('');
  const [flaggedThink, setFlaggedThink] = useState('');
  const [stream, setStream] = useState<Stream>('loading');
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [thinkOpen, setThinkOpen] = useState(true);
  const bodyRef = useRef<HTMLDivElement>(null);
  const hasHistory = useRef(false);

  const { answer, thinking } = useMemo(() => {
    const split = splitThinking(rawAnswer);
    return { answer: split.answer, thinking: (flaggedThink + split.thinking).trim() };
  }, [rawAnswer, flaggedThink]);

  useEffect(() => {
    if (answer.trim()) setThinkOpen(false);
  }, [answer]);

  useEffect(() => {
    const ctrl = new AbortController();
    let cancelled = false;
    setMessages([]);
    setRawAnswer('');
    setFlaggedThink('');
    setStream('loading');
    setThinkOpen(true);
    hasHistory.current = false;

    // 1. Saved conversation first.
    getMessages(conn, sessionId)
      .then((m) => {
        if (cancelled) return;
        setMessages(m);
        hasHistory.current = m.length > 0;
      })
      .catch(() => {})
      .finally(() => {
        if (cancelled) return;
        // 2. Attach the live stream (no-op if the run already finished).
        setStream((s) => (s === 'loading' ? 'connecting' : s));
        streamSession(
          conn,
          sessionId,
          (ev: StreamEvent) => {
            if (ev.kind === 'delta') {
              setStream('live');
              if (ev.thinking) setFlaggedThink((t) => t + ev.text);
              else setRawAnswer((t) => t + ev.text);
            } else if (ev.kind === 'event') {
              if (ev.type === 'tool_start') setStatusLine(`Running ${String(ev.raw.tool ?? 'tool')}...`);
              else if (ev.type === 'tool_output' || ev.type === 'model_info') setStatusLine(null);
            } else if (ev.kind === 'done') {
              setStatusLine(null);
              finishRun();
            }
          },
          {
            signal: ctrl.signal,
            onClose: () => setStream((s) => (s === 'live' || s === 'connecting' ? 'done' : s)),
            // A failed stream just means no live run -- not an error if we have
            // the saved conversation to show.
            onError: () => setStream(hasHistory.current ? 'inactive' : 'error'),
          },
        );
      });

    // When a live run completes, the server has saved the assistant message;
    // re-fetch so it lands as a normal bubble, then drop the live buffers.
    async function finishRun() {
      setStream('done');
      try {
        const m = await getMessages(conn, sessionId);
        if (!cancelled) setMessages(m);
      } catch {
        /* keep what we have */
      }
      if (!cancelled) {
        setRawAnswer('');
        setFlaggedThink('');
      }
    }

    return () => {
      cancelled = true;
      ctrl.abort(); // leaving detaches; the run keeps going server-side
    };
  }, [conn, sessionId]);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight });
  }, [messages, answer, thinking, thinkOpen, statusLine]);

  async function onStop() {
    setStopping(true);
    try {
      await stopSession(conn, sessionId);
      setStream('done');
    } catch (e) {
      console.warn('stop failed', e);
    } finally {
      setStopping(false);
    }
  }

  const isLive = stream === 'live' || stream === 'connecting';
  const liveBlock = Boolean(answer.trim() || thinking);
  const empty = stream !== 'loading' && messages.length === 0 && !liveBlock;

  return (
    <div className="screen detail">
      <header className="detail-header">
        <button className="ghost" onClick={onBack} type="button" aria-label="Back">
          <ChevronLeftIcon size={24} />
        </button>
        <span className={'status-pill ' + stream}>{stream}</span>
        {isLive && (
          <button className="stop" onClick={onStop} type="button" disabled={stopping}>
            {stopping ? 'Stopping...' : 'Stop'}
          </button>
        )}
      </header>

      <div className="detail-body" ref={bodyRef}>
        {messages.map((m, i) => (
          <div key={i} className={'msg msg-' + m.role}>
            <div className="msg-role">{m.role}</div>
            <pre className="msg-text">{m.content}</pre>
          </div>
        ))}

        {liveBlock && (
          <div className="msg msg-assistant">
            <div className="msg-role">assistant</div>
            {thinking && (
              <div className="think">
                <button
                  className="think-head"
                  type="button"
                  onClick={() => setThinkOpen((o) => !o)}
                >
                  <span className={'think-caret' + (thinkOpen ? ' open' : '')}>&gt;</span>
                  <span>Thinking</span>
                  {isLive && !answer.trim() && <span className="think-live">...</span>}
                </button>
                {thinkOpen && <pre className="think-text">{thinking}</pre>}
              </div>
            )}
            {answer.trim() && <pre className="msg-text">{answer}</pre>}
          </div>
        )}

        {stream === 'loading' && <div className="muted pad">Loading...</div>}
        {empty && (
          <div className="muted pad">
            {stream === 'error'
              ? 'Could not load this session.'
              : 'No messages yet.'}
          </div>
        )}
        {statusLine && <div className="tool-line">{statusLine}</div>}
      </div>
    </div>
  );
}
