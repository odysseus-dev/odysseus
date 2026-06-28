// Pure port helpers extracted so they're unit-testable without the
// browser-bound rest of cookbookRunning.js (issue #4507 follow-up).

// Read the port out of a serve launch command. Handles --port 8000,
// --port=8000, -p 8000, and -p=8000. Returns '' when none is present.
export function portOf(cmd) {
  const s = cmd || '';
  const m = s.match(/--port[=\s]+(\d+)/) || s.match(/(?:^|\s)-p[=\s]+(\d+)/);
  return m ? m[1] : '';
}

// Lowest free port >= start that isn't in usedPorts (array or Set of
// numbers/strings). Returns a string to match the serve command format.
export function nextFreePort(usedPorts, start = 8000) {
  const used = new Set([...usedPorts].map(p => parseInt(p, 10)));
  let port = start;
  while (used.has(port)) port++;
  return String(port);
}
