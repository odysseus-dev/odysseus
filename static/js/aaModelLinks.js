// Artificial Analysis deep links + canonical model identity for Cookbook.
// Resolves HF repo ids to AA slugs via /api/cookbook/aa-index.

const AA_BASE_URL = 'https://artificialanalysis.ai/models/';
const INDEX_URL = '/api/cookbook/aa-index';
const CACHE_KEY = 'odysseus_aa_index_v4';
const CACHE_TTL_MS = 24 * 60 * 60 * 1000;

let _aliases = null;
let _validSlugs = null;
let _loadPromise = null;
let _wiredRoot = null;

const _QUANT_TAIL_RE = /(\.gguf|\.bin|\.safetensors)$|(-(?:q\d+(?:_[a-z0-9]+)?|iq\d+(?:_xs|_s|_m|_l)?|awq(?:-\d+bit)?|gptq(?:-int\d+)?|fp\d+|nf\d+|mxfp\d+|int\d+|4bit|8bit|bf16|fp16|mlx-\d+bit|reap-\d+b-fp\d+))(?:$|-)/i;

const _COMPOUND_SUFFIX_RE = /(?:^|-)(?:qwen|llama|gemma|mistral|phi|codellama|minimax|nemotron|mixtral|olmo|gpt)(?:$|-)/i;

const _RELEASE_CANONICAL = [
  { re: /^DeepSeek-R1-0528(?:-.+)?$/i, display: 'DeepSeek-R1-0528', hfOrg: 'deepseek-ai' },
  { re: /^DeepSeek-R1-0120(?:-.+)?$/i, display: 'DeepSeek-R1-0120', hfOrg: 'deepseek-ai' },
];

const _FAMILY_BASE_ORG = [
  { re: /^gemma-/i, org: 'google' },
  { re: /^llama-/i, org: 'meta-llama' },
  { re: /^qwen/i, org: 'Qwen' },
  { re: /^deepseek-/i, org: 'deepseek-ai' },
];

const _PRIMARY_INSTRUCT_REPOS = {
  'qwen/qwen3-8b': 'Qwen3-8B-Instruct',
  'qwen/qwen3-4b': 'Qwen3-4B-Instruct',
  'qwen/qwen3-14b': 'Qwen3-14B-Instruct',
};

const _QUANT_PUBLISHERS = new Set([
  'solidrust', 'bartowski', 'unsloth', 'lmstudio-community', 'quanttrio',
  'mradermacher', 'huggingface', 'nousresearch', 'mlx-community', 'tensorblock',
]);

export function normalizeAaKey(raw) {
  if (!raw) return '';
  let s = String(raw).trim();
  if (s.includes('/')) s = s.split('/').pop();
  s = s.replace(/\.(gguf|bin|safetensors)$/i, '');
  for (let i = 0; i < 8; i++) {
    const nxt = s.replace(_QUANT_TAIL_RE, '');
    if (nxt === s) break;
    s = nxt;
  }
  s = s.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/-+/g, '-').replace(/^-|-$/g, '');
  return s;
}

function _stripQuantDisplayShort(short) {
  if (!short) return '';
  let s = String(short).replace(/\.(gguf|bin|safetensors)$/i, '');
  for (let i = 0; i < 8; i++) {
    const nxt = s.replace(_QUANT_TAIL_RE, '');
    if (nxt === s) break;
    s = nxt;
  }
  return s;
}

function _parentSlugAllowed(slugKey, key) {
  if (!key.startsWith(slugKey + '-')) return false;
  const suffix = key.slice(slugKey.length + 1);
  if (!suffix) return false;
  if (/^\d{4}(?:v\d+)?(?:$|-)/.test(suffix)) return false;
  if (_COMPOUND_SUFFIX_RE.test(suffix)) return false;
  if (suffix.includes('distill') && !slugKey.includes('distill')) return false;
  const parts = suffix.split('-').filter(Boolean);
  if (parts.length > 2) return false;
  return true;
}

function _pickSlug(slug) {
  if (!slug) return null;
  if (_validSlugs && !_validSlugs.has(slug)) return null;
  return slug;
}

export async function loadAaIndex() {
  if (_aliases) return _aliases;
  if (_loadPromise) return _loadPromise;
  _loadPromise = (async () => {
    const cached = _readSessionCache();
    if (cached?.aliases) {
      _aliases = cached.aliases;
      _validSlugs = cached.validSlugs || null;
      return _aliases;
    }
    try {
      const res = await fetch(INDEX_URL, { credentials: 'same-origin' });
      if (res.ok) {
        const data = await res.json();
        _aliases = data?.aliases && typeof data.aliases === 'object' ? data.aliases : {};
        _validSlugs = Array.isArray(data?.valid_slugs) ? new Set(data.valid_slugs) : null;
        _writeSessionCache(_aliases, _validSlugs);
        return _aliases;
      }
    } catch (_) {}
    _aliases = {};
    _validSlugs = null;
    return _aliases;
  })();
  return _loadPromise;
}

function _readSessionCache() {
  try {
    const raw = sessionStorage.getItem(CACHE_KEY);
    if (!raw) return null;
    const parsed = JSON.parse(raw);
    if (!parsed?.aliases || Date.now() - (parsed.ts || 0) > CACHE_TTL_MS) return null;
    return {
      aliases: parsed.aliases,
      validSlugs: Array.isArray(parsed.valid_slugs) ? new Set(parsed.valid_slugs) : null,
    };
  } catch (_) {
    return null;
  }
}

function _writeSessionCache(aliases, validSlugs) {
  try {
    sessionStorage.setItem(CACHE_KEY, JSON.stringify({
      ts: Date.now(),
      aliases,
      valid_slugs: validSlugs ? [...validSlugs] : [],
    }));
  } catch (_) {}
}

export function resolveAaSlug(modelId, aliases) {
  const map = aliases || _aliases;
  if (!map || !modelId) return null;
  const key = normalizeAaKey(modelId);
  if (!key) return null;

  if (map[key]) return _pickSlug(map[key]);

  const preferReasoning = /reasoning|thinking|r1|qwq/i.test(String(modelId));
  const parentMatches = Object.entries(map).filter(([k]) => _parentSlugAllowed(k, key));
  if (!parentMatches.length) return null;

  const scored = parentMatches
    .map(([k, slug]) => {
      let score = slug.length;
      if (preferReasoning && /reasoning|thinking/i.test(slug)) score += 50;
      if (!preferReasoning && !/reasoning|thinking/i.test(slug)) score += 50;
      if (k === key || slug === key) score += 100;
      return [score, slug];
    })
    .sort((a, b) => b[0] - a[0]);
  return _pickSlug(scored[0][1]);
}

function _inferCanonicalHfRepo(org, short) {
  const displayShort = _stripQuantDisplayShort(short);
  for (const rule of _RELEASE_CANONICAL) {
    if (rule.re.test(short)) {
      return `${rule.hfOrg}/${rule.display}`;
    }
  }
  if (org && _QUANT_PUBLISHERS.has(org.toLowerCase())) {
    for (const fam of _FAMILY_BASE_ORG) {
      if (fam.re.test(displayShort)) {
        return `${fam.org}/${displayShort}`;
      }
    }
  }
  return org && short ? `${org}/${short}` : null;
}

export function resolveModelIdentity(modelId, aliases) {
  const raw = String(modelId || '').trim();
  const slash = raw.indexOf('/');
  const org = slash >= 0 ? raw.slice(0, slash) : '';
  const short = slash >= 0 ? raw.slice(slash + 1) : raw;

  for (const rule of _RELEASE_CANONICAL) {
    if (rule.re.test(short)) {
      return {
        displayName: rule.display,
        hfRepo: `${rule.hfOrg}/${rule.display}`,
        aaSlug: resolveAaSlug(modelId, aliases),
      };
    }
  }

  const displayName = _primaryInstructDisplay(org, short)
    || _stripQuantDisplayShort(short) || short || raw;
  const hfRepo = _inferCanonicalHfRepo(org, short);

  return {
    displayName,
    hfRepo,
    aaSlug: resolveAaSlug(modelId, aliases),
  };
}

function _primaryInstructDisplay(org, short) {
  const repoKey = `${org}/${short}`.toLowerCase();
  return _PRIMARY_INSTRUCT_REPOS[repoKey] || null;
}

export function aaModelUrl(slug) {
  if (!slug) return null;
  return AA_BASE_URL + encodeURIComponent(slug);
}

export function renderModelNameLink(modelId, displayText, esc, aliases) {
  const identity = resolveModelIdentity(modelId, aliases);
  const text = displayText != null ? String(displayText) : identity.displayName;
  const slug = identity.aaSlug;
  let html = `<span class="cookbook-model-name">${esc(text)}</span>`;
  if (!slug) return html;
  const href = aaModelUrl(slug);
  html += (
    ` <a class="cookbook-aa-badge" href="${esc(href)}"`
    + ` target="_blank" rel="noopener noreferrer"`
    + ` title="View on Artificial Analysis (${esc(slug)})"`
    + ` data-aa-slug="${esc(slug)}"`
    + `>AA \u2197</a>`
  );
  return html;
}

export function wireAaNameClicks(root) {
  if (!root) return;
  if (_wiredRoot === root) return;
  _wiredRoot = root;
  root.addEventListener('click', (e) => {
    const link = e.target.closest('.cookbook-aa-badge, .cookbook-aa-name-link');
    if (!link) return;
    e.stopPropagation();
  });
}

export function resetAaIndexForTests() {
  _aliases = null;
  _validSlugs = null;
  _loadPromise = null;
  _wiredRoot = null;
  try { sessionStorage.removeItem(CACHE_KEY); } catch (_) {}
  try { sessionStorage.removeItem('odysseus_aa_index_v3'); } catch (_) {}
}

