// Shared read helper for the Odysseus <-> Hermes status, provider and project reads.
//   * Only GET (idempotent) reads are retried, a bounded number of times with backoff; state-changing calls never go through here.
//   * Failures are classified, so a sign-in problem is never confused with a slow or restarting service:
//       unauthorized  401/403, or a redirect to a login page   (never retried)
//       timeout       the request exceeded its time limit        (retried)
//       network       the server could not be reached            (retried)
//       unavailable   502/503/504                                (retried)
//       invalid       a reply that is not JSON (HTML, empty)     (retried; never treated as success)
//       http          any other error status                     (not retried)
//   * Identical concurrent reads share one request (sharedJson), and a caller's AbortSignal cancels only that caller's wait.

export const RETRYABLE = new Set(['timeout', 'network', 'unavailable', 'invalid']);

export class HttpError extends Error {
  constructor(kind, message, status = 0) {
    super(message);
    this.name = 'HttpError';
    this.kind = kind;
    this.status = status;
    this.retryable = RETRYABLE.has(kind);
  }
}

const sleep = (ms, signal) => new Promise((resolve, reject) => {
  const timer = setTimeout(resolve, ms);
  signal?.addEventListener('abort', () => { clearTimeout(timer); reject(signal.reason || new DOMException('Aborted', 'AbortError')); }, { once: true });
});

async function once(path, timeout, signal, label) {
  const timer = AbortSignal.timeout(timeout);
  const combined = signal && AbortSignal.any ? AbortSignal.any([signal, timer]) : (signal || timer);
  let response;
  try {
    response = await fetch(path, { credentials: 'same-origin', headers: { Accept: 'application/json' }, cache: 'no-store', signal: combined });
  } catch (error) {
    if (signal?.aborted) throw error;
    if (timer.aborted) throw new HttpError('timeout', `${label} did not answer within ${Math.round(timeout / 1000)} seconds.`);
    throw new HttpError('network', `${label} could not be reached.`);
  }
  if (response.status === 401 || response.status === 403 || (response.redirected && /login|signin|sign-in/i.test(response.url))) {
    throw new HttpError('unauthorized', 'Your Odysseus sign-in has ended. Sign in again.', response.status || 401);
  }
  const type = response.headers.get('content-type') || '';
  let body = null;
  if (/json/i.test(type)) { try { body = await response.json(); } catch (_) { body = null; } }
  if (!response.ok) {
    const kind = [502, 503, 504].includes(response.status) ? 'unavailable' : 'http';
    throw new HttpError(kind, (body && typeof body.detail === 'string' && body.detail) || `${label} failed (${response.status}).`, response.status);
  }
  if (body === null) throw new HttpError('invalid', `${label} returned an unexpected reply (not JSON).`, response.status);
  return body;
}

// retries: extra attempts after the first; delays double from `backoff` ms with a little jitter.
export async function getJson(path, { timeout = 10000, retries = 2, backoff = 500, signal, label = 'Odysseus' } = {}) {
  let failure;
  for (let attempt = 0; attempt <= retries; attempt++) {
    try { return await once(path, timeout, signal, label); }
    catch (error) {
      if (signal?.aborted || !(error instanceof HttpError) || !error.retryable) throw error;
      failure = error;
      if (attempt < retries) await sleep(backoff * 2 ** attempt + Math.random() * 100, signal);
    }
  }
  throw failure;
}

const flights = new Map();
// One in-flight read per key: callers arriving while it runs receive its result. A caller's own `signal` only abandons that caller's wait.
export function sharedJson(key, path, options = {}) {
  let flight = flights.get(key);
  if (!flight) {
    const { signal: _ignored, ...rest } = options;
    flight = getJson(path, rest).finally(() => flights.delete(key));
    flights.set(key, flight);
  }
  const { signal } = options;
  if (!signal) return flight;
  return new Promise((resolve, reject) => {
    if (signal.aborted) return reject(signal.reason || new DOMException('Aborted', 'AbortError'));
    signal.addEventListener('abort', () => reject(signal.reason || new DOMException('Aborted', 'AbortError')), { once: true });
    flight.then(resolve, reject);
  });
}

// Delay before the next scheduled check: the normal interval while healthy, doubling (capped) while failing. Never zero, never a storm.
export function nextDelay(failures, interval = 15000, cap = 60000, first = 3000) {
  return failures <= 0 ? interval : Math.min(cap, first * 2 ** (failures - 1));
}
