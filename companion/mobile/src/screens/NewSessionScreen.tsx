import { useEffect, useState } from 'react';
import type { Connection } from '../lib/connection';
import type { ChatOptions, ModelOption } from '../lib/api';
import { DEFAULT_OPTIONS, listModels, startSession } from '../lib/api';
import { ChevronLeftIcon } from '../components/icons';
import ToolToggles from '../components/ToolToggles';

// Compose + start a new chat from the phone. Pick a model (flattened from the
// server's endpoints), type a first message, send -> the server starts the run
// and we hand the new session id back so it opens in the stream view.
export default function NewSessionScreen({
  conn,
  onBack,
  onCreated,
}: {
  conn: Connection;
  onBack: () => void;
  onCreated: (sessionId: string) => void;
}) {
  const [models, setModels] = useState<ModelOption[]>([]);
  const [picked, setPicked] = useState(0);
  const [message, setMessage] = useState('');
  const [options, setOptions] = useState<ChatOptions>(DEFAULT_OPTIONS);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    listModels(conn)
      .then((m) => {
        setModels(m);
        setError(m.length ? null : 'No models available. Add one on your PC first.');
      })
      .catch(() => setError('Could not load models from the server.'));
  }, [conn]);

  async function send() {
    const model = models[picked];
    if (!model || !message.trim()) return;
    setBusy(true);
    setError(null);
    try {
      const { session_id } = await startSession(conn, {
        message: message.trim(),
        endpointId: model.endpointId,
        model: model.model,
        options,
      });
      onCreated(session_id);
    } catch (e) {
      setError('Could not start the chat. Is the model reachable?');
      console.warn('startSession failed', e);
      setBusy(false);
    }
  }

  return (
    <div className="screen compose">
      <header className="detail-header">
        <button className="ghost" onClick={onBack} type="button" aria-label="Back">
          <ChevronLeftIcon size={24} />
        </button>
        <span className="status-pill">New chat</span>
        <button
          className="stop send"
          onClick={send}
          type="button"
          disabled={busy || !message.trim() || models.length === 0}
        >
          {busy ? 'Starting...' : 'Send'}
        </button>
      </header>

      <div className="compose-body">
        <label className="field">
          <span>Model</span>
          <select
            value={picked}
            onChange={(e) => setPicked(Number(e.target.value))}
            disabled={models.length === 0}
          >
            {models.map((m, i) => (
              <option key={m.endpointId + m.model} value={i}>
                {m.model}
                {models.some((o) => o.model === m.model && o.endpointId !== m.endpointId)
                  ? ` (${m.endpointName})`
                  : ''}
              </option>
            ))}
          </select>
        </label>

        <label className="field grow">
          <span>Message</span>
          <textarea
            value={message}
            onChange={(e) => setMessage(e.target.value)}
            placeholder="Ask Odysseus something..."
            autoFocus
          />
        </label>

        <ToolToggles value={options} onChange={setOptions} disabled={busy} />

        {error && <div className="error">{error}</div>}
      </div>
    </div>
  );
}
