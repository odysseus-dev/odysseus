// Pure URL-resolution for Ollama serve tasks, split out of cookbookRunning.js so
// it can be unit-tested under node (cookbookRunning.js pulls in browser-only
// modules and can't load there). Stop/Kill cleanup uses this to target the
// daemon the task actually proxies to — get it wrong and Stop leaves the host
// Ollama loaded and the endpoint behind.

// Resolve the base URL (no trailing slash, no /v1) of the Ollama daemon a serve
// task talks to. Resolution order:
//   0. task.payload._ollamaBaseUrl → the host-gateway URL the backend returned
//      at launch for the in-container proxy case. Deterministic and independent
//      of pane-output timing, so a fast Stop/Kill before any runner line lands
//      still targets the host daemon instead of container loopback.
//   1. Native serve  → "Ollama API ready on port N: http://host:port"
//   2. Docker host-Ollama → "Ollama detected on host at host.docker.internal:port"
//      (Odysseus in a container proxying to the host daemon over the host
//      gateway — must NOT collapse to container loopback)
//   3. Fallback → OLLAMA_HOST=… port in the command, else 127.0.0.1:11434
export function _ollamaBaseUrlForTask(task, outputText = '') {
  const pinned = task?.payload?._ollamaBaseUrl;
  if (pinned) return String(pinned).replace(/\/+$/, '');
  const out = String(outputText || '');
  const ready = out.match(/Ollama API ready on port\s+\d+:\s*(http:\/\/[^\s]+)/i);
  if (ready) return ready[1].replace(/\/+$/, '');
  // Docker host-Ollama path: Odysseus runs in a container and proxies to the
  // host daemon over the host gateway. The runner logs this shape instead of
  // the "ready on port" URL, so unload/cleanup must target host.docker.internal
  // — NOT container loopback, which would leave the host daemon loaded and the
  // endpoint behind on Stop/Kill.
  const hostGw = out.match(/Ollama detected on host at\s+(host\.docker\.internal:\d+)/i);
  if (hostGw) return `http://${hostGw[1]}`;
  const cmd = String(task?.payload?._cmd || '');
  const host = cmd.match(/OLLAMA_HOST=([^\s]+)/)?.[1] || '';
  const port = host.match(/:(\d+)$/)?.[1] || '11434';
  return `http://127.0.0.1:${port}`;
}
