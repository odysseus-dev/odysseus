// Pure port helpers extracted so they're unit-testable without the
// browser-bound rest of cookbookRunning.js (issue #4507 follow-up).

// Read an explicitly requested port out of a serve launch command. Handles
// --port/-p and OLLAMA_HOST forms. Returns '' when the backend should choose.
export function portOf(cmd) {
  const s = cmd || '';
  const m = s.match(/--port[=\s]+(\d+)/)
    || s.match(/(?:^|\s)-p[=\s]+(\d+)/)
    || s.match(/OLLAMA_HOST\s*=\s*(?:['"])?(?:\[[^\]]+\]|[^:\s'"]+):(\d+)(?:['"])?/i);
  return m ? m[1] : '';
}

// Resolve the effective port persisted with a running serve task. The backend
// value wins because it may have selected a free Ollama port after the browser
// submitted the launch command.
export function taskServePort(task) {
  const persisted = task?.payload?.runtime_port ?? task?.payload?.port ?? task?.port;
  if (persisted != null && /^\d+$/.test(String(persisted))) return String(persisted);
  const cmd = task?.payload?._cmd || '';
  const explicit = portOf(cmd);
  if (explicit) return explicit;
  if (/\bollama\s+serve\b/i.test(cmd)) return '11434';
  if (/\bdocker\s+exec\s+(?:ollama-rocm|ollama-test)\b/i.test(cmd)) return '11434';
  return '';
}

// Merge server-authoritative launch metadata into the browser task payload.
// The server may normalize the command and choose a different port, so saving
// only the pre-request command leaves Stop looking at the wrong process.
export function withEffectiveServeMetadata(payload, response, fallbackCmd = '') {
  const next = { ...(payload || {}) };
  const rawCmd = response?.effective_cmd ?? response?.cmd;
  const responseCmd = typeof rawCmd === 'string' ? rawCmd.trim() : '';
  const cmd = responseCmd || String(fallbackCmd || '').trim();
  if (cmd) next._cmd = cmd;
  const rawPort = response?.runtime_port ?? response?.port;
  const port = Number(rawPort);
  if (rawPort != null && Number.isInteger(port) && port > 0 && port <= 65535) {
    next.runtime_port = String(port);
  }
  return next;
}

// Lowest free port >= start that isn't in usedPorts (array or Set of
// numbers/strings). Returns a string to match the serve command format.
export function nextFreePort(usedPorts, start = 8000) {
  const used = new Set([...usedPorts].map(p => parseInt(p, 10)));
  let port = start;
  while (used.has(port)) port++;
  return String(port);
}
