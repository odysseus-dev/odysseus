import { useEffect, useMemo, useRef, useState } from 'react';
import type { Connection } from '../lib/connection';
import type { ChatMsg, ChatOptions, ModelOption, StreamEvent } from '../lib/api';
import {
  DEFAULT_OPTIONS,
  getMessages,
  listModels,
  sendMessage,
  stopSession,
  streamSession,
  uploadFiles,
} from '../lib/api';
import { splitThinking } from '../lib/thinking';
import { ChevronLeftIcon } from '../components/icons';
import ToolToggles from '../components/ToolToggles';
import {
  AttachButton,
  AttachPreviews,
  AuthImage,
  PcFilesButton,
  type Pending,
} from '../components/Attachments';
import { FileIcon } from '../components/icons';
import FsBrowser from '../components/FsBrowser';
import VoiceButton from '../components/VoiceButton';

// A chat message plus, for the optimistic bubble of a just-sent turn, local
// preview URLs for any images attached (server history is text-only).
type LocalMsg = ChatMsg & { images?: string[] };

type Stream = 'loading' | 'connecting' | 'live' | 'done' | 'inactive' | 'error';

// Session view: a full conversation with a composer at the bottom, so the phone
// can carry a chat as far as the desktop can. Loads the saved history first (so
// ANY session opens, not just one with a live run), then attaches the live SSE
// on top. Sending a follow-up posts to /message and re-attaches the stream.
// The model picker in the header switches models mid-chat, like the desktop.
// Reasoning models' chain-of-thought shows in a collapsible "Thinking" block.
export default function SessionScreen({
  conn,
  sessionId,
  onBack,
}: {
  conn: Connection;
  sessionId: string;
  onBack: () => void;
}) {
  const [messages, setMessages] = useState<LocalMsg[]>([]);
  const [rawAnswer, setRawAnswer] = useState('');
  const [flaggedThink, setFlaggedThink] = useState('');
  const [stream, setStream] = useState<Stream>('loading');
  const [statusLine, setStatusLine] = useState<string | null>(null);
  const [stopping, setStopping] = useState(false);
  const [thinkOpen, setThinkOpen] = useState(true);
  const [models, setModels] = useState<ModelOption[]>([]);
  const [picked, setPicked] = useState(0);
  const [draft, setDraft] = useState('');
  const [options, setOptions] = useState<ChatOptions>(DEFAULT_OPTIONS);
  const [pending, setPending] = useState<Pending[]>([]);
  const [showBrowser, setShowBrowser] = useState(false);
  const [sending, setSending] = useState(false);
  const [sendError, setSendError] = useState<string | null>(null);
  // Bumped after a successful send to re-run the subscribe effect on the new run.
  const [runNonce, setRunNonce] = useState(0);
  const bodyRef = useRef<HTMLDivElement>(null);
  const hasHistory = useRef(false);
  const sessionIdRef = useRef(sessionId);

  const { answer, thinking } = useMemo(() => {
    const split = splitThinking(rawAnswer);
    return { answer: split.answer, thinking: (flaggedThink + split.thinking).trim() };
  }, [rawAnswer, flaggedThink]);

  useEffect(() => {
    if (answer.trim()) setThinkOpen(false);
  }, [answer]);

  // Model options for the in-chat picker (same source as the new-chat screen).
  useEffect(() => {
    listModels(conn).then(setModels).catch(() => {});
  }, [conn]);

  useEffect(() => {
    const ctrl = new AbortController();
    let cancelled = false;
    // Only wipe the transcript when the session itself changes -- a follow-up
    // (runNonce bump) keeps the bubbles already on screen.
    const sessionChanged = sessionIdRef.current !== sessionId;
    sessionIdRef.current = sessionId;
    if (sessionChanged) {
      setMessages([]);
      hasHistory.current = false;
    }
    setRawAnswer('');
    setFlaggedThink('');
    setStatusLine(null);
    setStream('loading');
    setThinkOpen(true);

    // 1. Saved conversation first.
    getMessages(conn, sessionId)
      .then((h) => {
        if (cancelled) return;
        setMessages(h.messages);
        hasHistory.current = h.messages.length > 0;
        // Default the picker to the session's current model the first time.
        if (h.model) {
          setModels((ms) => {
            const i = ms.findIndex((m) => m.model === h.model);
            if (i >= 0) setPicked(i);
            return ms;
          });
        }
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
        const h = await getMessages(conn, sessionId);
        if (!cancelled) setMessages(h.messages);
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
  }, [conn, sessionId, runNonce]);

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

  function removeAttachment(i: number) {
    setPending((p) => {
      const item = p[i];
      if (item?.kind === 'local') URL.revokeObjectURL(item.url);
      return p.filter((_, j) => j !== i);
    });
  }

  async function onSend() {
    const text = draft.trim();
    const model = models[picked];
    if ((!text && pending.length === 0) || sending || isLive) return;
    setSending(true);
    setSendError(null);
    const queued = pending;
    try {
      // Local files upload now; PC files already carry an attachment id.
      const locals = queued.filter((p) => p.kind === 'local');
      let attachments = queued.filter((p) => p.kind === 'remote').map((p) => p.id);
      if (locals.length) {
        const up = await uploadFiles(conn, locals.map((p) => (p as { file: File }).file));
        attachments = [...up.map((a) => a.id), ...attachments];
      }
      // Optimistically show the user's turn (local image previews stay valid for
      // this bubble); the run will stream the reply.
      const images = locals
        .filter((p) => (p as { file: File }).file.type.startsWith('image/'))
        .map((p) => (p as { url: string }).url);
      setMessages((m) => [...m, { role: 'user', content: text, images }]);
      setDraft('');
      setPending([]);
      await sendMessage(conn, sessionId, {
        message: text,
        endpointId: model?.endpointId,
        model: model?.model,
        options,
        attachments,
      });
      setRawAnswer('');
      setFlaggedThink('');
      setRunNonce((n) => n + 1); // re-attach the stream to the new run
    } catch (e) {
      setSendError('Could not send. Is the model reachable?');
      console.warn('sendMessage failed', e);
    } finally {
      setSending(false);
    }
  }

  const isLive = stream === 'live' || stream === 'connecting' || sending;
  const liveBlock = Boolean(answer.trim() || thinking);
  const empty = stream !== 'loading' && messages.length === 0 && !liveBlock;

  if (showBrowser) {
    return (
      <FsBrowser
        conn={conn}
        onClose={() => setShowBrowser(false)}
        onPick={(att) =>
          setPending((p) => [...p, { kind: 'remote', id: att.id, name: att.name, mime: att.mime }])
        }
      />
    );
  }

  return (
    <div className="screen detail">
      <header className="detail-header">
        <button className="ghost" onClick={onBack} type="button" aria-label="Back">
          <ChevronLeftIcon size={24} />
        </button>
        <select
          className="model-select"
          value={picked}
          onChange={(e) => setPicked(Number(e.target.value))}
          disabled={models.length === 0 || isLive}
          aria-label="Model"
        >
          {models.length === 0 && <option>model</option>}
          {models.map((m, i) => (
            <option key={m.endpointId + m.model} value={i}>
              {m.model}
              {models.some((o) => o.model === m.model && o.endpointId !== m.endpointId)
                ? ` (${m.endpointName})`
                : ''}
            </option>
          ))}
        </select>
        <span className={'status-dot ' + stream} title={stream} aria-label={stream} />
      </header>

      <div className="detail-body" ref={bodyRef}>
        {messages.map((m, i) => (
          <div key={i} className={'msg msg-' + m.role}>
            <div className="msg-role">{m.role}</div>
            {m.images && m.images.length > 0 && (
              <div className="msg-images">
                {m.images.map((src, j) => (
                  <img key={j} src={src} alt="attachment" />
                ))}
              </div>
            )}
            {m.attachments && m.attachments.length > 0 && (
              <div className="attach-previews">
                {m.attachments.map((a) => (
                  <div className="thumb" key={a.id}>
                    {a.mime.startsWith('image/') ? (
                      <AuthImage conn={conn} id={a.id} alt={a.name} />
                    ) : (
                      <span className="thumb-file">
                        <FileIcon size={18} />
                        <span className="thumb-name">{a.name}</span>
                      </span>
                    )}
                  </div>
                ))}
              </div>
            )}
            {m.content && <pre className="msg-text">{m.content}</pre>}
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
            {stream === 'error' ? 'Could not load this session.' : 'No messages yet.'}
          </div>
        )}
        {statusLine && <div className="tool-line">{statusLine}</div>}
      </div>

      {sendError && <div className="error">{sendError}</div>}

      <ToolToggles value={options} onChange={setOptions} disabled={isLive} />
      <AttachPreviews pending={pending} onRemove={removeAttachment} disabled={isLive} />

      <form
        className="composer"
        onSubmit={(e) => {
          e.preventDefault();
          onSend();
        }}
      >
        <AttachButton
          onPick={(picked) => setPending((p) => [...p, ...picked])}
          disabled={isLive}
        />
        <PcFilesButton onClick={() => setShowBrowser(true)} disabled={isLive} />
        <VoiceButton
          conn={conn}
          disabled={isLive}
          onText={(t) => setDraft((d) => (d ? d + ' ' + t : t))}
          onError={setSendError}
        />
        <textarea
          className="composer-input"
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' && !e.shiftKey) {
              e.preventDefault();
              onSend();
            }
          }}
          placeholder={isLive ? 'Running...' : 'Message Odysseus...'}
          rows={1}
          disabled={isLive}
        />
        {isLive ? (
          <button className="stop" onClick={onStop} type="button" disabled={stopping}>
            {stopping ? '...' : 'Stop'}
          </button>
        ) : (
          <button className="stop send" type="submit" disabled={!draft.trim() && pending.length === 0}>
            Send
          </button>
        )}
      </form>
    </div>
  );
}
