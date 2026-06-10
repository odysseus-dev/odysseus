// compare/backendStop.js
/**
 * Cancel a session's in-flight run on the server.
 *
 * Aborting a fetch only closes the SSE reader on the client — the run is
 * detached server-side, so the model keeps generating tokens upstream (e.g. in
 * LM Studio) until the backend is told to cancel it (issue #1508). Every place
 * that abandons a compare stream (the Stop buttons, closing compare, and the
 * idle timeout) must hit this, mirroring the main chat Stop button.
 *
 * A leaf module with no compare/ imports so panes.js, index.js and stream.js can
 * all use it without the circular-dependency dance, and so it's unit-testable.
 */
export function backendStopSession(sid) {
  if (!sid) return;
  return fetch(`/api/chat/stop/${encodeURIComponent(sid)}`, {
    method: 'POST', credentials: 'same-origin',
  }).catch(() => {});
}
