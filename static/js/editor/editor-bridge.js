// Browser side of the editor agent bridge. When the editor is open it
// subscribes to the app's SSE command stream and executes queued ops through
// the editor control API, then posts the result (+ a screenshot) back.
//
// Protocol (see routes/editor_agent_routes.py):
//   GET  /api/editor/bridge/events?session=<id>  -> SSE `data: {id,op,params}`
//   POST /api/editor/bridge/result {id,ok,result,error,screenshot,ms}
//
// The agent enqueues with POST /api/editor/bridge/command {session,steps},
// optionally with wait:true to block until this subscriber posts the result.

export const BRIDGE_VERSION = '1.0';

export function startEditorBridge({
  control,
  session = 'default',
  baseUrl = '',
  EventSourceImpl = null,
  fetchImpl = null,
  onCommand = null,
} = {}) {
  if (!control || typeof control.invoke !== 'function') {
    throw new Error('startEditorBridge requires a control with invoke()');
  }
  const ES = EventSourceImpl || (typeof EventSource !== 'undefined' ? EventSource : null);
  const doFetch = fetchImpl || (typeof fetch !== 'undefined' ? fetch : null);
  const eventUrl = `${baseUrl}/api/editor/bridge/events?session=${encodeURIComponent(session)}`;
  const resultUrl = `${baseUrl}/api/editor/bridge/result`;
  let source = null;
  let timer = null;
  let retry = 0;
  let stopped = false;

  // Execute one queued command and report the outcome (with an after screenshot).
  async function runAndReport(cmd) {
    const id = cmd && cmd.id;
    const started = Date.now();
    let ok = false;
    let result = null;
    let error = null;
    let screenshot = null;
    try {
      const out = await control.invoke(cmd.op, cmd.params || {});
      ok = !!(out && out.ok);
      result = out ? (out.result ?? null) : null;
      if (!ok) error = (out && out.error) || 'op failed';
    } catch (e) {
      error = String(e && e.message ? e.message : e);
    }
    try {
      const shot = await control.invoke('screenshot');
      screenshot = shot && shot.result ? shot.result.dataUrl : null;
    } catch (_) { /* screenshot is best-effort */ }
    const payload = { id, ok, result, error, screenshot, ms: Date.now() - started };
    if (onCommand) { try { onCommand(payload, cmd); } catch (_) {} }
    if (doFetch) {
      try {
        await doFetch(resultUrl, {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
      } catch (_) { /* the agent may have timed out; ignore */ }
    }
    return payload;
  }

  function connect() {
    if (stopped || !ES) return;
    try { source = new ES(eventUrl); } catch (_) { return; }
    source.onopen = () => { retry = 0; };
    source.onmessage = (ev) => {
      retry = 0;
      let cmd = null;
      try { cmd = JSON.parse(ev.data); } catch (_) { return; }
      if (!cmd || !cmd.op) return;
      // Fire-and-forget; results go back over /result, not the SSE stream.
      runAndReport(cmd);
    };
    source.onerror = () => {
      try { source && source.close(); } catch (_) {}
      if (stopped) return;
      retry += 1;
      timer = setTimeout(connect, Math.min(5000, 500 * retry));
    };
  }

  connect();

  return {
    version: BRIDGE_VERSION,
    session,
    eventUrl,
    resultUrl,
    runAndReport,
    stop() {
      stopped = true;
      try { source && source.close(); } catch (_) {}
      if (timer) clearTimeout(timer);
    },
  };
}

export default startEditorBridge;
