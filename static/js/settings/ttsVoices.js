// Provider-specific voice catalogs for Settings -> Text to Speech.
//
// OpenAI voices: https://platform.openai.com/docs/guides/text-to-speech
// (13 documented built-in voices; tts-1/tts-1-hd support the older subset).
// xAI voices: https://docs.x.ai/developers/model-capabilities/audio/text-to-speech
// (voice IDs verified against the documented Voice table; xAI's native API
// calls this field voice_id, not voice).

export const TTS_VOICE_SETS = Object.freeze({
  openai: Object.freeze([
    'alloy', 'ash', 'ballad', 'coral', 'echo', 'fable', 'nova', 'onyx',
    'sage', 'shimmer', 'verse', 'marin', 'cedar',
  ]),
  xai: Object.freeze([
    'carina', 'zagan', 'helix', 'orion', 'luna', 'iris', 'altair', 'zenith',
    'perseus', 'helios', 'lux', 'kepler', 'rigel', 'cosmo', 'celeste',
    'ursa', 'sirius', 'lumen', 'castor', 'naksh', 'atlas', 'aurora',
    'liora', 'ara', 'eve', 'leo', 'rex', 'sal',
  ]),
});

export const TTS_PROVIDER_DEFAULT_VOICE = Object.freeze({
  openai: 'alloy',
  openrouter: 'alloy',
  xai: 'eve',
});

export const TTS_PROVIDER_LABELS = Object.freeze({
  openai: 'OpenAI',
  openrouter: 'OpenRouter GPT Audio',
  xai: 'xAI / Grok',
  generic: 'Custom / generic',
  browser: 'Browser',
  disabled: 'Disabled',
});

// TTS-capable models advertised by each provider. These are unions with any
// TTS-shaped models an endpoint already lists, so an OpenAI endpoint without a
// refreshed model list still exposes TTS options, and an OpenRouter endpoint
// exposes GPT Audio even when its catalog has not returned those models yet.
export const TTS_MODEL_CATALOG = Object.freeze({
  openai: Object.freeze(['tts-1', 'tts-1-hd', 'gpt-4o-mini-tts']),
  openrouter: Object.freeze(['openai/gpt-audio', 'openai/gpt-audio-mini']),
  xai: Object.freeze(['xai-tts']),
});

const EXPLICIT_PROVIDERS = Object.freeze({
  openai: 'openai',
  'open-ai': 'openai',
  'openai.com': 'openai',
  openrouter: 'openrouter',
  'open-router': 'openrouter',
  'openrouter.ai': 'openrouter',
  xai: 'xai',
  'x-ai': 'xai',
  'x.ai': 'xai',
  grok: 'xai',
  browser: 'browser',
  disabled: 'disabled',
});

function _text(value) {
  return value == null ? '' : String(value).trim().toLowerCase();
}

function _hostMatch(text, host) {
  var escaped = host.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp('(^|[^a-z0-9])' + escaped + '([/:?#]|$)').test(text);
}

function _tokenMatch(text, token) {
  var escaped = token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  return new RegExp('(^|[^a-z0-9])' + escaped + '($|[^a-z0-9])').test(text);
}

function _asList(value) {
  if (Array.isArray(value)) return value.filter(Boolean);
  return value ? [value] : [];
}

// Normalize an explicit provider label. UI values such as "endpoint:<id>" and
// "custom" are intentionally not treated as a provider on their own; inference
// combines them with endpoint metadata and model data.
export function normalizeTtsProvider(value) {
  var raw = _text(value);
  if (!raw || raw.indexOf('endpoint:') === 0) return '';
  if (EXPLICIT_PROVIDERS[raw]) return EXPLICIT_PROVIDERS[raw];
  if (raw.indexOf('openai') !== -1 && raw.indexOf('compatible') === -1) return 'openai';
  if (raw.indexOf('grok') !== -1 || _tokenMatch(raw, 'xai')) return 'xai';
  return '';
}

// Infer which provider's voice catalog applies to the current TTS selection.
// `input` may be a provider string or an object with any of: provider, kind,
// providerType/type, endpoint metadata, endpointUrl/baseUrl/url, model, models,
// name. Explicit endpoint metadata wins over URL/model/name hints.
export function inferTtsProvider(input) {
  if (input == null) return 'generic';
  if (typeof input === 'string') input = { provider: input };

  var endpoint = input.endpoint && typeof input.endpoint === 'object'
    ? input.endpoint
    : {};

  var provider = normalizeTtsProvider(input.provider);
  if (provider === 'browser' || provider === 'disabled' ||
      provider === 'openai' || provider === 'openrouter' || provider === 'xai') {
    return provider;
  }

  var explicit = [
    input.providerType, input.provider_type, input.type,
    endpoint.provider, endpoint.provider_type, endpoint.providerType, endpoint.type,
  ];
  for (var i = 0; i < explicit.length; i += 1) {
    var detected = normalizeTtsProvider(explicit[i]);
    if (detected && detected !== 'browser' && detected !== 'disabled') return detected;
  }

  var urlText = [
    input.endpointUrl, input.url, input.baseUrl,
    endpoint.base_url, endpoint.baseUrl, endpoint.url,
  ].filter(Boolean).join(' ').toLowerCase();
  if (urlText) {
    if (_hostMatch(urlText, 'x.ai') || _tokenMatch(urlText, 'grok')) return 'xai';
    // OpenRouter GPT Audio uses OpenAI-style voices.
    if (_hostMatch(urlText, 'openrouter.ai')) return 'openai';
    if (_hostMatch(urlText, 'openai.com')) return 'openai';
  }

  var models = _asList(input.models).concat(_asList(endpoint.models));
  var modelText = [input.model, input.modelId, endpoint.model]
    .concat(models).filter(Boolean).join(' ').toLowerCase();
  if (modelText) {
    if (modelText.indexOf('x-ai/') !== -1 || _tokenMatch(modelText, 'grok') ||
        _tokenMatch(modelText, 'xai')) return 'xai';
    if (_tokenMatch(modelText, 'tts-1') || modelText.indexOf('gpt-4o-mini-tts') !== -1 ||
        modelText.indexOf('openai/tts') !== -1 || modelText.indexOf('gpt-audio') !== -1) return 'openai';
  }

  var nameText = [input.name, endpoint.name].filter(Boolean).join(' ').toLowerCase();
  if (nameText) {
    if (_tokenMatch(nameText, 'xai') || _tokenMatch(nameText, 'grok') ||
        nameText.indexOf('x.ai') !== -1) return 'xai';
    if (_tokenMatch(nameText, 'openrouter') || nameText.indexOf('open router') !== -1) return 'openai';
    if (_tokenMatch(nameText, 'openai') &&
        nameText.indexOf('openai-compatible') === -1 &&
        nameText.indexOf('openai compatible') === -1) return 'openai';
  }

  return 'generic';
}

// Return a copy of the catalog for a recognized provider, or null when the
// provider has no finite list (custom/generic, browser, disabled).
function _voiceCatalogKey(provider) {
  var key = normalizeTtsProvider(provider);
  // OpenRouter GPT Audio uses OpenAI-style voice IDs.
  if (key === 'openrouter') return 'openai';
  return key;
}

export function voiceOptionsForProvider(provider) {
  var voices = TTS_VOICE_SETS[_voiceCatalogKey(provider)];
  return voices ? voices.slice() : null;
}

export function defaultVoiceForProvider(provider) {
  return TTS_PROVIDER_DEFAULT_VOICE[_voiceCatalogKey(provider)] || '';
}

function _isMusicModel(model) {
  return /(^|[\/_.-])lyria([\/_.-]|$)/.test(_text(model));
}

// True for models that produce speech (not music or text-only chat models).
export function isTtsModel(model) {
  var text = _text(model);
  if (!text || _isMusicModel(text)) return false;
  return /(^|[\/_.-])tts([\/_.-]|$)|gpt-audio/.test(text);
}

export function ttsProviderForEndpoint(endpoint) {
  if (!endpoint || typeof endpoint !== 'object') return '';
  var urlText = [endpoint.base_url, endpoint.baseUrl, endpoint.url]
    .filter(Boolean).join(' ').toLowerCase();
  if (urlText && _hostMatch(urlText, 'openrouter.ai')) return 'openrouter';
  var provider = inferTtsProvider({
    provider: endpoint.id ? 'endpoint:' + endpoint.id : '',
    endpoint: endpoint,
    models: endpoint.models,
  });
  if (provider === 'browser' || provider === 'disabled') return '';
  return provider;
}

// Return only speech-capable model IDs for an endpoint. OpenRouter always offers
// the GPT Audio catalog, even if /models has not listed it yet; music models
// such as google/lyria-* are never included.
export function ttsModelsForEndpoint(endpoint) {
  if (!endpoint || typeof endpoint !== 'object') return [];

  var provider = ttsProviderForEndpoint(endpoint);
  var endpointModels = Array.isArray(endpoint.models) ? endpoint.models : [];
  var models = [];

  function add(model) {
    var value = String(model == null ? '' : model).trim();
    if (value && models.indexOf(value) === -1) models.push(value);
  }

  (TTS_MODEL_CATALOG[provider] || []).forEach(add);

  endpointModels.forEach(function(model) {
    if (!isTtsModel(model)) return;
    var lower = _text(model);
    if (provider === 'openrouter' && lower.indexOf('gpt-audio') === -1) return;
    if (provider === 'openai' && lower.indexOf('gpt-audio') !== -1) return;
    add(model);
  });

  return models;
}

export function isTtsCapableEndpoint(endpoint) {
  if (!endpoint || typeof endpoint !== 'object' || endpoint.is_enabled === false) return false;
  var provider = ttsProviderForEndpoint(endpoint);
  if (provider === 'openai' || provider === 'openrouter' || provider === 'xai') return true;
  return ttsModelsForEndpoint(endpoint).length > 0;
}

export function isTtsModelForProvider(provider, model) {
  var key = normalizeTtsProvider(provider);
  var text = String(model == null ? '' : model).trim();
  if (!text) return false;
  if (key === 'openrouter') return isTtsModel(text) && _text(text).indexOf('gpt-audio') !== -1;
  if (key === 'openai') return isTtsModel(text) && _text(text).indexOf('gpt-audio') === -1;
  if (key === 'xai') return isTtsModel(text);
  return isTtsModel(text);
}
