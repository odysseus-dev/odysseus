import { useEffect, useMemo, useRef, useState } from 'react';
import type { Connection } from '../lib/connection';
import type { StreamEvent } from '../lib/api';
import { stopSession, streamSession } from '../lib/api';
import { splitThinking } from '../lib/thinking';
import { ChevronLeftIcon } from '../components/icons';

// The remote-control detail view: connect to a session's live SSE, render the
// streaming answer, surface tool/status events, and offer a Stop button. Opening
// mid-run replays everything so far (the server buffers it), then streams live.
//
// Reasoning models stream their chain-of-thought two ways: flagged
// (delta + thinking:true, from Odysseus' llm_core) or inline (<think>...</think>
// in the normal text). We collect both into a collapsible "Thinking" block,
// matching the desktop UI, and keep the answer itself clean.
export default function SessionScreen({
  conn,
  sessionId,
  onBack,
}: {
  conn: Connection;
  sessionId: string;
  onBack: () => void;
}) {
  // rawAnswer = non-flagged stream (may contain inline <think> tags);
  // flaggedThink = deltas the server flagged thinking:true.
  const [rawAnswer, setRawAnswer] = useState('');
  const [flaggedThink, setFlaggedThink] = useState('');
  const [status, setStatus] = useState<'connecting' | 'live' | 'done' | 'error'>(
    'connecting',
  );
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [thinkOpen, setThinkOpen] = useState(true);
  const bodyRef = useRef<HTMLDivElement>(null);

  const { answer, thinking } = useMemo(() => {
    const split = splitThinking(rawAnswer);
    return { answer: split.answer, thinking: (flaggedThink + split.thinking).trim() };
  }, [rawAnswer, flaggedThink]);

  // Once a real answer starts arriving, fold the thinking away (like the PC).
  useEffect(() => {
    if (answer.trim()) setThinkOpen(false);
  }, [answer]);

  useEffect(() => {
    const ctrl = new AbortController();
    setRawAnswer('');
    setFlaggedThink('');
    setStatus('connecting');
    setThinkOpen(true);

    streamSession(
      conn,
      sessionId,
      (ev: StreamEvent) => {
        if (ev.kind === 'delta') {
          setStatus('live');
          if (ev.thinking) setFlaggedThink((t) => t + ev.text);
          else setRawAnswer((t) => t + ev.text);
        } else if (ev.kind === 'event') {
          if (ev.type === 'tool_start') setStatusLine(`Running ${String(ev.raw.tool ?? 'tool')}...`);
          else if (ev.type === 'tool_output' || ev.type === 'model_info') setStatusLine(null);
        } else if (ev.kind === 'done') {
          setStatus('done');
          setStatusLine(null);
        }
      },
      {
        signal: ctrl.signal,
        onClose: () => setStatus((s) => (s === 'live' || s === 'connecting' ? 'done' : s)),
        onError: () => setStatus('error'),
      },
    );

    return () => ctrl.abort(); // leaving the screen detaches; the run keeps going
  }, [conn, sessionId]);

  useEffect(() => {
    bodyRef.current?.scrollTo({ top: bodyRef.current.scrollHeight });
  }, [answer, thinking, thinkOpen, statusLine]);

  async function onStop() {
    setStopping(true);
    try {
      await stopSession(conn, sessionId);
      setStatus('done');
    } catch (e) {
      console.warn('stop failed', e);
    } finally {
      setStopping(false);
    }
  }

  const hasOutput = Boolean(answer.trim() || thinking);

  return (
    <div className="screen detail">
      <header className="detail-header">
        <button className="ghost" onClick={onBack} type="button" aria-label="Back">
          <ChevronLeftIcon size={24} />
        </button>
        <span className={'status-pill ' + status}>{status}</span>
        <button
          className="stop"
          onClick={onStop}
          type="button"
          disabled={stopping || status === 'done'}
        >
          {stopping ? 'Stopping...' : 'Stop'}
        </button>
      </header>

      <div className="detail-body" ref={bodyRef}>
        {thinking && (
          <div className="think">
            <button
              className="think-head"
              type="button"
              onClick={() => setThinkOpen((o) => !o)}
            >
              <span className={'think-caret' + (thinkOpen ? ' open' : '')}>&gt;</span>
              <span>Thinking</span>
              {status === 'live' && !answer.trim() && <span className="think-live">...</span>}
            </button>
            {thinkOpen && <pre className="think-text">{thinking}</pre>}
          </div>
        )}

        {answer.trim() && <pre className="stream-text">{answer}</pre>}

        {!hasOutput && (
          <div className="muted pad">
            {status === 'error'
              ? 'Could not open the stream. The session may not be running.'
              : 'Waiting for output...'}
          </div>
        )}
        {statusLine && <div className="tool-line">{statusLine}</div>}
      </div>
    </div>
  );
}
