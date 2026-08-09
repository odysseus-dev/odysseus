// Pure capability helpers for per-chat reasoning and verbosity controls.

const AUTO_ONLY = ['auto'];
const OPENAI_VERBOSITY = ['auto', 'low', 'medium', 'high'];
const THINKING_MODEL_PATTERNS = [
  'qwen3', 'qwq', 'deepseek-r1', 'deepseek-v3.1', 'deepseek-reasoner',
  'minimax', 'm2-reap', 'gemma', 'stepfun', 'step-3', 'step3',
  'magistral', 'mistral-small', 'mistral-medium', 'gpt-oss',
];

function parseEndpoint(raw) {
  try {
    return new URL(String(raw || '').trim());
  } catch (_) {
    return null;
  }
}

function normalizedPath(endpoint) {
  const path = endpoint.pathname || '';
  return path.length > 1 ? path.replace(/\/+$/, '') : path;
}

function hostMatches(hostname, expected) {
  const host = String(hostname || '').toLowerCase();
  return host === expected || host.endsWith(`.${expected}`);
}

export function isChatGptSubscriptionEndpoint(raw) {
  if (String(raw || '').trim().toLowerCase() === 'chatgpt-subscription') return true;
  const endpoint = parseEndpoint(raw);
  if (!endpoint || endpoint.hostname.toLowerCase() !== 'chatgpt.com') return false;
  const path = normalizedPath(endpoint).toLowerCase();
  return path === '/backend-api/codex' || path.startsWith('/backend-api/codex/');
}

export function isOllamaEndpoint(raw) {
  const endpoint = parseEndpoint(raw);
  if (!endpoint) return false;
  if (hostMatches(endpoint.hostname, 'ollama.com')) return true;
  const host = endpoint.hostname.toLowerCase();
  const localHost = ['localhost', '127.0.0.1', '0.0.0.0', '::1'].includes(host)
    || endpoint.port === '11434';
  if (!localHost) return false;
  const path = normalizedPath(endpoint).toLowerCase();
  return path === '' || path === '/api' || path.startsWith('/api/')
    || path === '/v1' || path.startsWith('/v1/');
}

function modelId(model) {
  return String(model || '').trim().toLowerCase().split('/').pop();
}

function gpt5MinorVersion(model) {
  const match = modelId(model).match(/^gpt[-_]?5(?:\.(\d+))?(?=$|[-_:])/);
  return match ? Number.parseInt(match[1] || '0', 10) : null;
}

function openAiReasoningValues(model) {
  const id = modelId(model);
  const version = gpt5MinorVersion(model);
  if (version != null) {
    if (/^gpt[-_]?5(?:\.\d+)?-pro(?=$|[-_:])/.test(id)) {
      return version === 0 ? ['high'] : ['medium', 'high', 'xhigh'];
    }
    if (/^gpt[-_]?5(?:\.\d+)?-codex(?=$|[-_:])/.test(id)) {
      const values = ['low', 'medium', 'high'];
      if (version >= 2 || id.includes('-codex-max')) values.push('xhigh');
      return values;
    }
    if (version === 0) return ['minimal', 'low', 'medium', 'high'];
    if (version === 1) return ['none', 'low', 'medium', 'high'];
    const values = ['none', 'low', 'medium', 'high', 'xhigh'];
    if (version >= 6) values.push('max');
    return values;
  }
  return /^(?:o1|o3|o4)(?=$|[-_:])/.test(id) ? ['low', 'medium', 'high'] : [];
}

function isThinkingModel(model) {
  const value = String(model || '').toLowerCase();
  return THINKING_MODEL_PATTERNS.some(pattern => value.includes(pattern));
}

export function modelControlCapabilities(key, { model = '', endpointUrl = '' } = {}) {
  const unsupported = reason => ({ supported: false, allowed: [...AUTO_ONLY], reason });
  if (!model) return unsupported('Select a model first');

  if (key === 'reasoning_effort') {
    if (isChatGptSubscriptionEndpoint(endpointUrl)) {
      const values = openAiReasoningValues(model);
      if (values.length) {
        return {
          supported: true,
          allowed: ['auto', ...values.map(value => value === 'none' ? 'off' : value)],
          reason: '',
        };
      }
    }
    if (isOllamaEndpoint(endpointUrl) && isThinkingModel(model)) {
      const allowed = String(model).toLowerCase().includes('gpt-oss')
        ? ['auto', 'low', 'medium', 'high']
        : ['auto', 'off', 'on'];
      return { supported: true, allowed, reason: '' };
    }
    return unsupported('Reasoning controls are unavailable for this model endpoint');
  }

  if (key === 'verbosity') {
    if (isChatGptSubscriptionEndpoint(endpointUrl) && gpt5MinorVersion(model) != null) {
      return { supported: true, allowed: [...OPENAI_VERBOSITY], reason: '' };
    }
    return unsupported('Verbosity controls are unavailable for this model endpoint');
  }

  return unsupported('');
}
