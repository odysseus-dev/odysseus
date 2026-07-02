// Shared DOM-free provider device-flow runner.

export const PROVIDER_DEVICE_FLOWS = {
  copilot: {
    label: 'GitHub Copilot',
    startUrl: '/api/copilot/device/start',
    pollUrl: '/api/copilot/device/poll',
    authUrl(start) {
      return start?.verification_uri_complete || start?.verification_uri || '';
    },
  },
  'chatgpt-subscription': {
    label: 'ChatGPT Subscription',
    startUrl: '/api/chatgpt-subscription/device/start',
    pollUrl: '/api/chatgpt-subscription/device/poll',
    authUrl(start) {
      return start?.verification_uri || '';
    },
  },
};

function _formData() {
  if (typeof FormData !== 'undefined') return new FormData();
  return new URLSearchParams();
}

async function _jsonOrEmpty(response) {
  try {
    return await response.json();
  } catch (_) {
    return {};
  }
}

function _messageFromPayload(payload, fallback) {
  if (payload && typeof payload.detail === 'string' && payload.detail.trim()) {
    return payload.detail.trim();
  }
  if (payload && typeof payload.error === 'string' && payload.error.trim()) {
    return payload.error.trim();
  }
  if (payload && typeof payload.message === 'string' && payload.message.trim()) {
    return payload.message.trim();
  }
  return fallback;
}

export function formatDeviceFlowError(error, fallback = 'Request failed') {
  if (!error) return fallback;
  if (typeof error === 'string') return error;
  if (error.detail) return String(error.detail);
  if (error.message) return String(error.message);
  return fallback;
}

async function _fetchJson(fetchImpl, url, options, fallback) {
  const response = await fetchImpl(url, options);
  const payload = await _jsonOrEmpty(response);
  if (!response.ok) {
    throw new Error(_messageFromPayload(payload, fallback || `Request failed (HTTP ${response.status})`));
  }
  return payload;
}

function _defaultSleep(ms) {
  return new Promise(resolve => setTimeout(resolve, ms));
}

async function _callCallback(fn, payload) {
  if (typeof fn === 'function') await fn(payload);
}

export async function runProviderDeviceFlow(provider, options = {}) {
  const cfg = PROVIDER_DEVICE_FLOWS[provider];
  if (!cfg) throw new Error(`Unknown device-flow provider: ${provider}`);

  const fetchImpl = options.fetchImpl || globalThis.fetch?.bind(globalThis);
  if (!fetchImpl) throw new Error('Fetch API is unavailable');

  const openWindow = options.openWindow || ((url) => {
    if (globalThis.window && typeof globalThis.window.open === 'function') {
      globalThis.window.open(url, '_blank', 'noopener');
    }
  });
  const sleep = options.sleep || _defaultSleep;
  const now = options.now || (() => Date.now());
  const formData = options.formData || _formData();

  const start = await _fetchJson(fetchImpl, cfg.startUrl, {
    method: 'POST',
    body: formData,
    credentials: 'same-origin',
  }, `Failed to start ${cfg.label} sign-in`);

  if (!start.poll_id) throw new Error(`${cfg.label} sign-in did not return a poll id`);
  const authUrl = cfg.authUrl(start);
  await _callCallback(options.onStart, { provider, config: cfg, start, authUrl });
  if (authUrl) openWindow(authUrl);

  const deadline = now() + Number(start.expires_in || 900) * 1000;
  let stepMs = Math.max(Number(start.interval || 5), 2) * 1000;

  while (true) {
    if (now() > deadline) return { status: 'expired' };
    await _callCallback(options.onWaiting, { provider, config: cfg, start, authUrl });
    await sleep(stepMs);
    if (now() > deadline) return { status: 'expired' };

    const fd = _formData();
    fd.append('poll_id', start.poll_id);
    const poll = await _fetchJson(fetchImpl, cfg.pollUrl, {
      method: 'POST',
      body: fd,
      credentials: 'same-origin',
    }, `${cfg.label} sign-in poll failed`);
    await _callCallback(options.onPoll, { provider, config: cfg, start, poll });

    if (poll.status === 'authorized') {
      return { status: 'authorized', endpoint: poll.endpoint || {} };
    }
    if (poll.status === 'failed') {
      return { status: 'failed', error: poll.error || 'denied' };
    }
    if (poll.interval) {
      stepMs = Math.max(Number(poll.interval || 5), 2) * 1000;
    }
  }
}
