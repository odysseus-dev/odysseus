#!/usr/bin/env node

import fs from 'node:fs';
import path from 'node:path';
import crypto from 'node:crypto';
import { fileURLToPath } from 'node:url';
import babelParser from '@babel/parser';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const I18N_DIR = path.join(ROOT, 'static', 'i18n');
const STEAM_LANGUAGE_SOURCE = 'https://partner.steamgames.com/doc/store/localization/languages';
const { parse: parseJavaScript } = babelParser;

const HTML_FILES = ['static/index.html', 'static/login.html'];
const JSON_UI_FILES = ['static/manifest.json'];
const PYTHON_ROOTS = ['app.py', 'routes', 'companion', 'src', 'services'];
const SOURCE_EXTENSIONS = new Set(['.js', '.mjs', '.ts', '.tsx', '.jsx']);
const JS_EXCLUDES = [
  /\/static\/lib\//,
  /\.min\.js$/,
  /\/static\/js\/i18n\.js$/,
  /\/static\/js\/modelCatalog\.js$/,
  /\/static\/js\/mimoModels\.js$/,
  /\/static\/js\/mimoProviders\.generated\.js$/,
  /\/node_modules\//,
];

const LOCALES = Object.freeze({
  ar: { name: 'العربية', dir: 'rtl' },
  bg: { name: 'български език', dir: 'ltr' },
  'zh-CN': { name: '简体中文', dir: 'ltr' },
  'zh-TW': { name: '繁體中文', dir: 'ltr' },
  cs: { name: 'Čeština', dir: 'ltr' },
  da: { name: 'Dansk', dir: 'ltr' },
  nl: { name: 'Nederlands', dir: 'ltr' },
  en: { name: 'English', dir: 'ltr' },
  fi: { name: 'Suomi', dir: 'ltr' },
  fr: { name: 'Français', dir: 'ltr' },
  de: { name: 'Deutsch', dir: 'ltr' },
  el: { name: 'Ελληνικά', dir: 'ltr' },
  hu: { name: 'Magyar', dir: 'ltr' },
  id: { name: 'Bahasa Indonesia', dir: 'ltr' },
  it: { name: 'Italiano', dir: 'ltr' },
  ja: { name: '日本語', dir: 'ltr' },
  ko: { name: '한국어', dir: 'ltr' },
  ms: { name: 'Bahasa Melayu', dir: 'ltr' },
  no: { name: 'Norsk', dir: 'ltr' },
  pl: { name: 'Polski', dir: 'ltr' },
  pt: { name: 'Português', dir: 'ltr' },
  'pt-BR': { name: 'Português-Brasil', dir: 'ltr' },
  ro: { name: 'Română', dir: 'ltr' },
  ru: { name: 'Русский', dir: 'ltr' },
  es: { name: 'Español-España', dir: 'ltr' },
  'es-419': { name: 'Español-Latinoamérica', dir: 'ltr' },
  sv: { name: 'Svenska', dir: 'ltr' },
  th: { name: 'ไทย', dir: 'ltr' },
  tr: { name: 'Türkçe', dir: 'ltr' },
  uk: { name: 'Українська', dir: 'ltr' },
  vi: { name: 'Tiếng Việt', dir: 'ltr' },
});

const ALIASES = Object.freeze({
  zh: 'zh-CN',
  'zh-Hans': 'zh-CN',
  'zh-SG': 'zh-CN',
  'zh-Hant': 'zh-TW',
  'zh-HK': 'zh-TW',
  'zh-MO': 'zh-TW',
  'pt-PT': 'pt',
  nb: 'no',
  nn: 'no',
  'nb-NO': 'no',
  'nn-NO': 'no',
  in: 'id',
  'es-ES': 'es',
  'es-AR': 'es-419',
  'es-BO': 'es-419',
  'es-CL': 'es-419',
  'es-CO': 'es-419',
  'es-CR': 'es-419',
  'es-CU': 'es-419',
  'es-DO': 'es-419',
  'es-EC': 'es-419',
  'es-GT': 'es-419',
  'es-HN': 'es-419',
  'es-MX': 'es-419',
  'es-NI': 'es-419',
  'es-PA': 'es-419',
  'es-PE': 'es-419',
  'es-PR': 'es-419',
  'es-PY': 'es-419',
  'es-SV': 'es-419',
  'es-US': 'es-419',
  'es-UY': 'es-419',
  'es-VE': 'es-419',
});

// Semantic keys for UI that is created after page load. Source extraction
// cannot safely infer ownership for these values, so keep the small explicit
// contract next to the catalog tooling.
const CORE_MESSAGES = Object.freeze({
  'auth.first_time_setup': 'First-time setup — create your admin account',
  'auth.create_admin_account': 'Create Admin Account',
  'auth.create_account': 'Create Account',
  'auth.already_have_account': 'Already have an account?',
  'auth.passwords_do_not_match': 'Passwords do not match',
  'auth.password_minimum': 'Password must be at least {0} characters',
  'auth.username_reserved': 'This username is reserved',
  'auth.invalid_code': 'Invalid code',
  'auth.login_failed': 'Login failed',
  'auth.account_creation_failed': 'Account creation failed',
  'auth.two_factor_code': '2FA Code',
  'auth.two_factor_placeholder': 'Enter 6-digit code',
  'auth.two_factor_aria': 'Two-factor authentication code',
  'auth.verify': 'Verify',
  'auth.hide_password': 'Hide password',
  'auth.too_many_requests': 'Too many requests — try again later',
  'auth.already_configured': 'Already configured',
  'auth.username_required': 'Username is required',
  'auth.setup_failed': 'Setup failed',
  'auth.run_setup_first': 'Run setup first',
  'auth.registration_disabled': 'Registration is disabled. Ask an admin for an account.',
  'auth.username_taken': 'Username already taken',
  'auth.invalid_credentials': 'Invalid credentials',
  'auth.invalid_two_factor_code': 'Invalid 2FA code',
  'ui.email.folder.inbox': 'INBOX',
  'ui.email.folder.sent': 'Sent',
  'ui.email.folder.flagged': 'Starred',
  'ui.email.folder.archive': 'Archive',
  'ui.email.folder.all': 'All Mail',
  'ui.email.folder.junk': 'Junk',
  'ui.email.folder.trash': 'Trash',
  'ui.email.folder.drafts': 'Drafts',
  'ui.email.folder.scheduled': 'Scheduled',
  'ui.save.this.memory': 'Save this memory',
  'ui.loading.chats': 'Loading chats…',
  'ui.chats.unavailable': 'Chats unavailable',
  'ui.no.email.body.to.summarize': 'No email body to summarize',
  'ui.no.model.configured.for.email.summaries': 'No model configured for email summaries',
  'ui.the.model.returned.an.empty.summary': 'The model returned an empty summary',
  'ui.session.request.failed.http.value': 'Session request failed (HTTP {0})',
  'ui.session.request.returned.an.invalid.response': 'Session request returned an invalid response',
  'ui.welcome.tip.search_chats': 'Tip: Press Ctrl+K to search across all your conversations.',
  'ui.welcome.tip.toggle_sidebar': 'Tip: Press Ctrl+B to quickly toggle the sidebar.',
  'ui.welcome.tip.move_sidebar': 'Tip: Shift-click the sidebar toggle to swap it to the other side.',
  'ui.welcome.tip.drop_files': 'Tip: Drag and drop files onto the chat to attach them.',
  'ui.welcome.tip.session_menu': 'Tip: Right-click a session for rename, delete, and memory options.',
  'ui.welcome.tip.session_menu_touch': 'Tip: Long-press a session for rename, delete, and memory options.',
  'ui.welcome.tip.nobody_mode': 'Tip: Tap the eye icon for Nobody mode — no history saved.',
  'ui.welcome.tip.agent_mode': 'Tip: Switch to Agent mode for web search and code execution.',
  'ui.welcome.tip.compare_mode': 'Tip: Use Compare mode to test different models side by side.',
  'ui.welcome.tip.attach_files': 'Tip: Attach images or files using the + button next to the input.',
  'css.copied': '✓ Copied',
  'css.editing': 'EDITING',
  'css.drop_to_attach': 'Drop to attach',
  'css.write_email': 'Write your email…',
  'css.planning_goal': 'AI is planning your goal…',
  'css.no_title': 'No title',
  'ui.language_changed': 'Language changed.',
});

const BRANDS = Object.freeze([
  'Odysseus',
  'OpenAI',
  'ChatGPT',
  'Codex',
  'Anthropic',
  'Claude',
  'Google',
  'Gemini',
  'GitHub',
  'Gmail',
  'Microsoft',
  'Outlook',
  'Matrix',
  'Discord',
  'Slack',
  'Notion',
  'Box',
  'Figma',
  'Atlassian',
  'Rovo',
  'SharePoint',
  'Teams',
  'Obsidian',
  'Hugging Face',
  'Ollama',
  'SearXNG',
  'DuckDuckGo',
  'Brave',
  'Playwright',
  'Chromium',
  'llama.cpp',
  'Perplexity',
  'Copilot',
  'Groq',
  'Tavily',
  'Mistral',
  'DeepSeek-V4-Flash',
  'DeepSeek-V4',
  'DeepSeek',
  'Qwen3.5',
  'Qwen',
  'OpenRouter',
  'PyTorch',
  'xAI',
]);

const STABLE_TOKENS = Object.freeze([
  'AI',
  'API',
  'JSON',
  'HTML',
  'CSS',
  'JavaScript',
  'TypeScript',
  'Python',
  'Rust',
  'OAuth',
  'MCP',
  'URL',
  'HTTP',
  'HTTPS',
  'PDF',
  'PWA',
  'TOTP',
  'CalDAV',
  'IMAP',
  'SMTP',
  'WebSocket',
  'SSE',
  'SQL',
  'Markdown',
  'CSV',
  'ZIP',
  'safetensors',
  'vLLM',
  'SGLang',
  'CUDA',
  'ROCm',
  'GGUF',
  'skills.sh',
  'MLX',
  'NCCL',
  'FlashInfer',
  'Triton',
  'tmux',
  'mmproj',
  'CardDAV',
  'torch',
  'rembg',
  'llama-cpp-python',
  'hf_...',
]);

const PLACEHOLDER = /\{([A-Za-z_][A-Za-z0-9_]*|\d+)\}/g;
const BIDI_CONTROLS = /[\u061c\u200e\u200f\u202a-\u202e\u2066-\u2069]/u;
const FORMAT_CONTROL = /\p{Cf}/u;
const HTML_TAG = /<\/?[a-z][^>]*>/iu;
const MACHINE_MARKER = /ZXQ|QXZ|ZXXZ|ZXZ|QLOCK/iu;
const SCRIPT_PATTERNS = Object.freeze({
  Arabic: /\p{Script=Arabic}/u,
  Cyrillic: /\p{Script=Cyrillic}/u,
  Greek: /\p{Script=Greek}/u,
  Han: /\p{Script=Han}/u,
  Hiragana: /\p{Script=Hiragana}/u,
  Katakana: /\p{Script=Katakana}/u,
  Hangul: /\p{Script=Hangul}/u,
  Thai: /\p{Script=Thai}/u,
});
const LOCALE_SCRIPTS = Object.freeze({
  ar: new Set(['Arabic']),
  bg: new Set(['Cyrillic']),
  el: new Set(['Greek']),
  ja: new Set(['Han', 'Hiragana', 'Katakana']),
  ko: new Set(['Han', 'Hangul']),
  ru: new Set(['Cyrillic']),
  th: new Set(['Thai']),
  uk: new Set(['Cyrillic']),
  'zh-CN': new Set(['Han']),
  'zh-TW': new Set(['Han']),
});
const EXACT_TOKEN_PATTERNS = [
  /\{(?:[A-Za-z_][A-Za-z0-9_]*|\d+)\}/gu,
  /\{[A-Za-z_][A-Za-z0-9_]*![rsa]\}/gu,
  /&(?:#\d+|#x[0-9a-f]+|[a-z][a-z0-9]+);/giu,
  /\{\{[\s\S]*?\}\}/gu,
];
const SOURCE_LITERAL_PATTERNS = [
  /\b(?:https?|wss?):\/\/[^\s<>"')\]]+/giu,
  /(?<![:/\w])(?:~\/|\/)(?:[A-Za-z0-9_.@+-]+\/)*[A-Za-z0-9_.@+-]+/gu,
  /(?<![\w-])--[A-Za-z][\w-]*/gu,
  /\b(?:localhost|(?:[a-z0-9-]+\.)+[a-z]{2,})(?::\d+)?(?:\/[A-Za-z0-9._~:/?#@!$&'()*+,;=%-]*)?/giu,
  /\b[A-Za-z][A-Za-z0-9]*_[A-Za-z0-9_]+\b/gu,
  /\b[a-z]+(?:[A-Z][A-Za-z0-9]*)+\b/g,
  /\{(?:(?:\s*"(?:[^"\\]|\\.)*"\s*:\s*"(?:[^"\\]|\\.)*"\s*,?)+)\s*\}/gu,
  /"(?:pip|python3?|curl|docker|sudo|uv|export|--)[^"]*"/gu,
  /\bpip3?\s+install\s+"[^"\r\n]+"/gu,
  /\bpip3?\s+install\b/gu,
  /\bcurl\b[^\r\n]*?\|\s*sh\b/gu,
  /\bcurl\b/gu,
  /\|\s*sh\b/gu,
  /\b[A-Za-z0-9._-]+\[[A-Za-z0-9._-]+\]/gu,
  /\blist\/search\/view\/add\/update\/delete\/toggle_item\b/gu,
  /`[^`\r\n]+`/gu,
  /\{[a-z_]+(?:,\s*[a-z_]+)+\}/gu,
  /\{[a-z_]+ or '[^']*'\}/gu,
];
const CSS_LITERAL = /^(?:style\s*=\s*["'])?(?:(?:--[a-z0-9_-]+|-?(?:webkit|moz)-[a-z-]+|(?:align|animation|aspect|backdrop|background|border|bottom|box|clip|color|cursor|display|filter|flex|float|font|gap|grid|height|inset|justify|left|line|margin|max|min|object|opacity|outline|overflow|padding|pointer|position|right|text|top|touch|transform|transition|user|vertical|visibility|white|width|word|z-index)[a-z-]*)\s*:[^;]*)(?:;\s*(?:--[a-z0-9_-]+|-?[a-z][a-z0-9-]*)\s*:[^;]*)*;?(?:["'])?$/iu;
const FONT_FACE_LITERAL = /^@font-face\s*\{[\s\S]*\}$/iu;
const SELECTOR_LITERAL = /^(?:[#.][A-Za-z_-][\s\S]*|\[(?:data-|id[$^*|~]?=)[\s\S]*|(?:select|div)[.#\[][\s\S]*)$/u;
const SHELL_OR_CONFIG_LITERAL = /^(?:[A-Z_][A-Z0-9_]*\s*=|(?:capture-pane|curl|docker|du|has-session|kill-session|pip|pkill|python3?|uv|powershell)\s|tmux\s+kill-session\s|Remove-Item\s|sudo\s|export\s+(?:[A-Z_]|\{)|--[a-z]|-[a-z]$|\{(?:[A-Za-z_][A-Za-z0-9_]*|\d+)\}\s+-m\s|import\s+|(?:bash|npx|python3?)$)/u;
const CLASS_LIST_LITERAL = /^(?=[a-z0-9_-]*(?:-|_))[a-z][a-z0-9_-]*(?: [a-z][a-z0-9_-]*)+$/u;
const JINJA_BLOCK = /\{%[\s\S]*?%\}/u;
const MUSTACHE = /\{\{[\s\S]*?\}\}/gu;
const SLASH_COMMAND = /^\/[a-z][a-z0-9-]*(?:\s+(?:\[[^\]]+\]|<[^>]+>))*$/iu;
const HTML_ENTITIES = Object.freeze({
  amp: '&',
  darr: '↓',
  ge: '≥',
  gt: '>',
  larr: '←',
  lsaquo: '‹',
  lt: '<',
  mdash: '—',
  middot: '·',
  minus: '−',
  nbsp: '\u00a0',
  quot: '"',
  rarr: '→',
  rsaquo: '›',
  times: '×',
  uarr: '↑',
});

function readJson(file, fallback = null) {
  try {
    return JSON.parse(fs.readFileSync(file, 'utf8'));
  } catch {
    return fallback;
  }
}

function writeJson(file, value) {
  fs.mkdirSync(path.dirname(file), { recursive: true });
  fs.writeFileSync(file, `${JSON.stringify(value, null, 2)}\n`);
}

function walk(target) {
  if (!fs.existsSync(target)) return [];
  const stat = fs.statSync(target);
  if (stat.isFile()) return [target];
  return fs.readdirSync(target, { withFileTypes: true })
    .flatMap(entry => walk(path.join(target, entry.name)));
}

function relative(file) {
  return path.relative(ROOT, file).split(path.sep).join('/');
}

function normalizeSource(raw) {
  return String(raw ?? '')
    .replace(/\r\n?/g, '\n')
    .replace(/[\t\n ]+/g, ' ')
    .trim();
}

function decodeHtmlEntities(raw) {
  return String(raw).replace(
    /&(?:#(\d+)|#x([0-9a-f]+)|([a-z][a-z0-9]+));/giu,
    (entity, decimal, hexadecimal, named) => {
      if (decimal) return String.fromCodePoint(Number(decimal));
      if (hexadecimal) return String.fromCodePoint(Number.parseInt(hexadecimal, 16));
      return HTML_ENTITIES[named.toLowerCase()] ?? entity;
    },
  );
}

function looksUserFacing(raw) {
  const value = normalizeSource(decodeHtmlEntities(raw));
  if (value.length < 2 || value.length > 600 || !/[A-Za-z]/.test(value)) return false;
  if (isCodeLiteral(value) || /<\/?[a-z][^>]*>/iu.test(value)) return false;
  if (/^\{[^{}]+\}$/u.test(value) || /\{[^{}]*(?:[().]|::)[^{}]*\}/u.test(value)) return false;
  if (/^(?:\\[0-9a-f]{2,6})$/iu.test(value)) return false;
  if (/^[a-z][a-z0-9]*(?:-[a-z0-9]+)+$/u.test(value)) return false;
  if (/^[A-Za-z]+Error$/u.test(value)) return false;
  if (/^\d+(?:\.\d+)?x$/iu.test(value)) return false;
  if (/^(?:-?\d+(?:\.\d+)?(?:px|rem|em|vh|vw|%|ms|s)?(?:\s|$)|rgba?\(|hsla?\(|color-mix\(|var\(--|calc\()/iu.test(value)) return false;
  if (/\b(?:rgba?|hsla?|color-mix|linear-gradient|radial-gradient|box-shadow|translate[XY]?|scale[XY]?|rotate)\s*\(/iu.test(value)) return false;
  if (/^(?:https?:|data:|blob:|mailto:|tel:|\/api\/|\/static\/|\.\/|\.\.\/)/iu.test(value)) return false;
  if (/^(?:#[\w-]+|\.[\w-]+|--[\w-]+|[\w-]+\.(?:js|css|py|rs|ts|tsx|json|md|html|svg|png|jpe?g|gif|webp|woff2?))$/iu.test(value)) return false;
  if (/^[a-z][a-zA-Z0-9]*(?:\.[a-zA-Z0-9_-]+)+$/u.test(value)) return false;
  if (/^[a-z][a-zA-Z0-9_]*$/u.test(value) && /[A-Z_]/u.test(value)) return false;
  if (/^[A-Za-z_$][\w$-]*$/u.test(value) && /[_$]|[a-z][A-Z]/u.test(value)) return false;
  if (/^[\w-]+\/[\w./-]+$/u.test(value)) return false;
  if (/^[\w.-]+@[\w.-]+$/u.test(value)) return false;
  if (/^[{}[\]().,:;!?+*=|&%$#@~`"'\\/-]+$/u.test(value)) return false;
  const symbols = (value.match(/[{}[\]<>_=\\/]/gu) || []).length;
  if (symbols > Math.max(6, value.length / 5)) return false;
  if (/\\[bBdDsSwW]/u.test(value) && /[*+?{}[\]()]/u.test(value)) return false;
  if (/^(?:GET|POST|PUT|PATCH|DELETE) \/\S+/u.test(value)) return false;
  return true;
}

function slugFor(source) {
  const withoutPlaceholders = source.replace(PLACEHOLDER, ' value ');
  const slug = withoutPlaceholders
    .normalize('NFKD')
    .replace(/[\u0300-\u036f]/gu, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/gu, '.')
    .replace(/^\.+|\.+$/gu, '')
    .split('.')
    .filter(Boolean)
    .slice(0, 10)
    .join('.')
    .slice(0, 72);
  return `ui.${slug || 'message'}`;
}

function hashText(value) {
  return crypto.createHash('sha256').update(value).digest('hex');
}

function placeholders(value) {
  return [...String(value).matchAll(PLACEHOLDER)].map(match => match[1]).sort();
}

function tokenCount(value, token) {
  const escaped = token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');
  const boundary = /^[A-Za-z0-9]+$/.test(token)
    ? `(?<![A-Za-z0-9])${escaped}(?![A-Za-z0-9])`
    : escaped;
  return [...String(value).matchAll(new RegExp(boundary, 'gu'))].length;
}

function tokens(value, pattern) {
  pattern.lastIndex = 0;
  return [...String(value).matchAll(pattern)]
    .map(match => match[0].replace(/[.,;:!?]+$/u, ''))
    .sort();
}

function sameTokenMultiset(source, target, pattern) {
  return JSON.stringify(tokens(source, pattern)) === JSON.stringify(tokens(target, pattern));
}

function preservesSourceTokens(source, target, pattern) {
  const counts = values => values.reduce((result, token) => {
    result.set(token, (result.get(token) || 0) + 1);
    return result;
  }, new Map());
  const expected = counts(tokens(source, pattern));
  const actual = counts(tokens(target, pattern));
  return [...expected].every(([token, count]) => actual.get(token) === count);
}

function isCodeLiteral(source) {
  const value = String(source ?? '').trim();
  if (!value) return false;
  const expressions = [...value.matchAll(MUSTACHE)];
  const rawTemplate = JINJA_BLOCK.test(value)
    || (expressions.length && /^(?:model|user)?$/u.test(value.replace(MUSTACHE, '').trim()));
  let structuredJson = false;
  try {
    const parsed = JSON.parse(value);
    structuredJson = parsed !== null && typeof parsed === 'object';
  } catch {
    // Natural text can contain braces; only valid JSON is opaque.
  }
  return value === 'ms)'
    || CSS_LITERAL.test(value)
    || FONT_FACE_LITERAL.test(value)
    || SELECTOR_LITERAL.test(value)
    || SHELL_OR_CONFIG_LITERAL.test(value)
    || CLASS_LIST_LITERAL.test(value)
    || SLASH_COMMAND.test(value)
    || structuredJson
    || rawTemplate;
}

function structurallyValid(source, target) {
  if (typeof target !== 'string' || !target.trim()) return false;
  if (isCodeLiteral(source)) return target === source;
  if (
    BIDI_CONTROLS.test(target)
    || FORMAT_CONTROL.test(target)
    || HTML_TAG.test(target)
    || MACHINE_MARKER.test(target)
  ) return false;
  if (JSON.stringify(placeholders(source)) !== JSON.stringify(placeholders(target))) return false;
  if (!EXACT_TOKEN_PATTERNS.every(pattern => sameTokenMultiset(source, target, pattern))) return false;
  if (!SOURCE_LITERAL_PATTERNS.every(pattern => preservesSourceTokens(source, target, pattern))) return false;
  return [...BRANDS, ...STABLE_TOKENS].every(
    token => tokenCount(source, token) === tokenCount(target, token),
  );
}

function makeCollector(existingEnglish = {}) {
  const bySource = new Map();
  const byKey = new Map(Object.entries(existingEnglish));
  for (const [key, source] of Object.entries(existingEnglish)) {
    bySource.set(source, key);
    bySource.set(decodeHtmlEntities(source), key);
  }
  const entries = new Map();

  function keyFor(source) {
    if (bySource.has(source)) return bySource.get(source);
    const base = slugFor(source);
    if (!byKey.has(base) || byKey.get(base) === source) {
      byKey.set(base, source);
      bySource.set(source, base);
      return base;
    }
    const suffix = crypto.createHash('sha1').update(source).digest('hex').slice(0, 8);
    const key = `${base}.${suffix}`;
    byKey.set(key, source);
    bySource.set(source, key);
    return key;
  }

  function add(raw, file, line, kind = 'literal') {
    const source = normalizeSource(decodeHtmlEntities(raw));
    if (!looksUserFacing(source)) return;
    const key = keyFor(source);
    const record = entries.get(key) || { key, source, kind, locations: [] };
    const location = `${relative(file)}:${line || 1}`;
    if (!record.locations.includes(location)) record.locations.push(location);
    if (record.kind !== kind) record.kind = 'mixed';
    entries.set(key, record);
  }

  return { add, entries };
}

function maskHtmlBlock(raw) {
  const newlines = String(raw).match(/\n/gu)?.join('') || '';
  return `<i18n-skip></i18n-skip>${newlines}`;
}

function extractHtmlText(raw, file, baseLine, collector, kind = 'html') {
  const withoutComments = raw
    .replace(/<!--[\s\S]*?-->/gu, maskHtmlBlock)
    .replace(/<script\b[\s\S]*?<\/script>/giu, maskHtmlBlock)
    .replace(/<style\b[\s\S]*?<\/style>/giu, maskHtmlBlock)
    .replace(/<(?:code|pre)\b[\s\S]*?<\/(?:code|pre)>/giu, maskHtmlBlock);

  const attrPattern = /\b(?:placeholder|title|aria-label|aria-description|alt)\s*=\s*(["'])([\s\S]*?)\1/giu;
  let match;
  while ((match = attrPattern.exec(withoutComments))) {
    const line = baseLine + withoutComments.slice(0, match.index).split('\n').length - 1;
    collector.add(match[2], file, line, `${kind}-attribute`);
  }

  const textPattern = />([^<>]+)</gu;
  while ((match = textPattern.exec(withoutComments))) {
    const line = baseLine + withoutComments.slice(0, match.index).split('\n').length - 1;
    collector.add(match[1], file, line, `${kind}-text`);
  }
}

function templateSource(node) {
  let value = '';
  node.quasis.forEach((quasi, index) => {
    value += quasi.value.cooked ?? quasi.value.raw;
    if (index < node.expressions.length) value += `{${index}}`;
  });
  return value;
}

function memberName(node) {
  if (!node) return '';
  if (node.type === 'Identifier') return node.name;
  if (node.type === 'StringLiteral') return node.value;
  if (node.type === 'MemberExpression' || node.type === 'OptionalMemberExpression') {
    return `${memberName(node.object)}.${memberName(node.property)}`;
  }
  return '';
}

const UI_PROPERTIES = new Set([
  'text', 'textContent', 'innerText', 'innerHTML', 'label', 'title', 'tooltip',
  'placeholder', 'description', 'message', 'help', 'hint', 'caption', 'heading',
  'aria-label', 'ariaLabel', 'aria-description', 'ariaDescription', 'alt',
  'emptyText', 'errorText', 'loadingText', 'confirmText', 'cancelText',
]);
const UI_CALLS = /(?:^|\.)(?:h|createElement|setAttribute|showToast|toast|notify|alert|confirm|prompt|showError|showMessage|setStatus|setMessage|setText|openConfirm|openPrompt|styledConfirm|styledPrompt|renderMenu|showChooser|showCommands|addOption|addItem)$/iu;
const UI_CONTAINER_NAMES = /^(?:.*(?:label|title|text|message|description|tooltip|placeholder|help|hint|caption|heading|tabs?|options?|actions?|commands?|menus?|statuses|errors?|empty|loading|confirm|cancel).*)$/iu;

function isUiContext(node, ancestors) {
  let current = node;
  for (let index = ancestors.length - 1, depth = 0; index >= 0 && depth < 6; index -= 1, depth += 1) {
    const parent = ancestors[index];
    if ((parent.type === 'ObjectProperty' || parent.type === 'ObjectMethod') && parent.value === current) {
      const key = memberName(parent.key);
      if (UI_PROPERTIES.has(key) || UI_CONTAINER_NAMES.test(key)) return true;
    }
    if (parent.type === 'JSXAttribute') {
      const name = memberName(parent.name);
      if (UI_PROPERTIES.has(name)) return true;
    }
    if (parent.type === 'AssignmentExpression' && parent.right === current) {
      const leaf = memberName(parent.left).split('.').at(-1);
      if (UI_PROPERTIES.has(leaf) || UI_CONTAINER_NAMES.test(leaf)) return true;
    }
    if (parent.type === 'CallExpression') {
      const name = memberName(parent.callee);
      if (UI_CALLS.test(name) || UI_CONTAINER_NAMES.test(name.split('.').at(-1))) return true;
    }
    if (parent.type === 'VariableDeclarator') {
      const name = memberName(parent.id);
      if (UI_CONTAINER_NAMES.test(name)) return true;
    }
    current = parent;
  }
  return false;
}

function isLikelyStandaloneMessage(value) {
  if (value.length < 4) return false;
  return /^(?:Loading|Saving|Saved|Failed|Unable|Error|Warning|Delete|Remove|Add|Create|Edit|Open|Close|Cancel|Confirm|Search|Select|Choose|No |Show|Hide|Enable|Disable|Copy|Copied|Download|Upload|Export|Import|Refresh|Retry|Start|Stop|Run|Running|Ready|Connected|Disconnected|Unknown)\b/iu.test(value);
}

function skipStringNode(node, parent) {
  if (!parent) return false;
  if (['ImportDeclaration', 'ExportNamedDeclaration', 'ExportAllDeclaration'].includes(parent.type)) return true;
  if ((parent.type === 'ObjectProperty' || parent.type === 'ObjectMethod') && parent.key === node && !parent.computed) return true;
  if ((parent.type === 'MemberExpression' || parent.type === 'OptionalMemberExpression') && parent.property === node && !parent.computed) return true;
  if (parent.type === 'Directive' || parent.type === 'DirectiveLiteral') return true;
  if (
    parent.type === 'BinaryExpression'
    && ['==', '!=', '===', '!=='].includes(parent.operator)
  ) {
    const other = parent.left === node ? parent.right : parent.right === node ? parent.left : null;
    if (other?.type === 'UnaryExpression' && other.operator === 'typeof') return true;
  }
  return parent.type === 'CallExpression' && parent.callee?.type === 'Import';
}

function isConsoleContext(ancestors) {
  return ancestors.some(parent => (
    parent.type === 'CallExpression'
    && /^console\./u.test(memberName(parent.callee))
  ));
}

function visitAst(node, ancestors, callback) {
  if (!node || typeof node !== 'object') return;
  if (typeof node.type === 'string') callback(node, ancestors);
  const nextAncestors = typeof node.type === 'string' ? [...ancestors, node] : ancestors;
  for (const [key, value] of Object.entries(node)) {
    if (['loc', 'start', 'end', 'extra', 'errors', 'comments', 'tokens'].includes(key)) continue;
    if (Array.isArray(value)) value.forEach(child => visitAst(child, nextAncestors, callback));
    else if (value && typeof value === 'object') visitAst(value, nextAncestors, callback);
  }
}

function extractJavaScript(file, collector) {
  const code = fs.readFileSync(file, 'utf8');
  let ast;
  try {
    ast = parseJavaScript(code, {
      sourceType: 'unambiguous',
      allowAwaitOutsideFunction: true,
      allowReturnOutsideFunction: true,
      errorRecovery: true,
      plugins: [
        'jsx', 'typescript', 'decorators-legacy', 'classProperties',
        'classPrivateProperties', 'classPrivateMethods', 'dynamicImport',
        'importMeta', 'topLevelAwait',
      ],
    });
  } catch (error) {
    process.stderr.write(`parse warning: ${relative(file)}: ${error.message}\n`);
    return;
  }

  visitAst(ast, [], (node, ancestors) => {
    const parent = ancestors.at(-1);
    if (node.type === 'StringLiteral') {
      if (skipStringNode(node, parent) || isConsoleContext(ancestors)) return;
      const value = node.value;
      const line = node.loc?.start.line || 1;
      if (/<[a-z][\s\S]*>/iu.test(value)) {
        extractHtmlText(value, file, line, collector, 'js-string-html');
      } else if (isUiContext(node, ancestors) || isLikelyStandaloneMessage(normalizeSource(value))) {
        collector.add(value, file, line, 'js-string');
      }
    } else if (node.type === 'TemplateLiteral') {
      if (parent?.type === 'TaggedTemplateExpression' || isConsoleContext(ancestors)) return;
      const source = templateSource(node);
      const line = node.loc?.start.line || 1;
      if (/<[a-z][\s\S]*>/iu.test(source)) {
        extractHtmlText(source, file, line, collector, 'js-template-html');
      } else if (isUiContext(node, ancestors) || isLikelyStandaloneMessage(normalizeSource(source))) {
        collector.add(source, file, line, 'js-template');
      }
    } else if (node.type === 'JSXText') {
      collector.add(node.value, file, node.loc?.start.line, 'jsx-text');
    }
  });
}

function extractQuotedSource(file, collector) {
  const code = fs.readFileSync(file, 'utf8');
  const triplePattern = /"""([\s\S]*?)"""|'''([\s\S]*?)'''/gu;
  let match;
  const htmlRanges = [];
  while ((match = triplePattern.exec(code))) {
    const raw = match[1] ?? match[2] ?? '';
    if (/<(?:html|body|main|div|form|h1|h2|p|button|label|input)\b/iu.test(raw)) {
      extractHtmlText(raw, file, code.slice(0, match.index).split('\n').length, collector, 'server-html');
    }
    htmlRanges.push([match.index, triplePattern.lastIndex]);
  }
  const lines = code.split('\n');
  const context = /(?:HTTPException|JSONResponse|HTMLResponse|detail\s*=|["'](?:error|message|detail|status|title|description|label)["']\s*:|raise\s+(?:ValueError|RuntimeError)|return\s+\{)/u;
  const quoted = /(["'])((?:\\.|(?!\1).){2,600})\1/gu;
  let offset = 0;
  for (let index = 0; index < lines.length; index += 1) {
    const line = lines[index];
    const inTriple = htmlRanges.some(([start, end]) => offset >= start && offset < end);
    offset += line.length + 1;
    if (inTriple || !context.test(line)) continue;
    while ((match = quoted.exec(line))) {
      const decoded = match[2].replace(/\\n/gu, ' ').replace(/\\t/gu, ' ').replace(/\\(["'])/gu, '$1');
      if (isLikelyStandaloneMessage(normalizeSource(decoded))) collector.add(decoded, file, index + 1, 'server-string');
    }
    quoted.lastIndex = 0;
  }
}

function extractCss(file, collector) {
  const code = fs.readFileSync(file, 'utf8');
  const pattern = /\bcontent\s*:\s*(["'])(.*?)\1/gu;
  let match;
  while ((match = pattern.exec(code))) {
    collector.add(match[2], file, code.slice(0, match.index).split('\n').length, 'css-content');
  }
}

function sourceFiles() {
  return walk(path.join(ROOT, 'static'))
    .filter(file => SOURCE_EXTENSIONS.has(path.extname(file)))
    .filter(file => !JS_EXCLUDES.some(pattern => pattern.test(file)))
    .sort();
}

function buildSourceSnapshot() {
  const englishFile = path.join(I18N_DIR, 'en.json');
  const collector = makeCollector(readJson(englishFile, {}));
  for (const name of HTML_FILES) {
    const file = path.join(ROOT, name);
    if (fs.existsSync(file)) extractHtmlText(fs.readFileSync(file, 'utf8'), file, 1, collector);
  }
  for (const name of JSON_UI_FILES) {
    const file = path.join(ROOT, name);
    const data = readJson(file, {});
    for (const key of ['name', 'short_name', 'description']) collector.add(data[key], file, 1, 'json-metadata');
  }
  for (const file of sourceFiles()) extractJavaScript(file, collector);
  for (const root of PYTHON_ROOTS) {
    for (const file of walk(path.join(ROOT, root))) {
      if (file.endsWith('.py')) extractQuotedSource(file, collector);
    }
  }
  extractCss(path.join(ROOT, 'static', 'style.css'), collector);
  for (const [key, source] of Object.entries(CORE_MESSAGES)) {
    collector.entries.set(key, {
      key,
      source,
      kind: 'semantic',
      locations: ['scripts/i18n-catalog.mjs:CORE_MESSAGES'],
    });
  }
  const records = [...collector.entries.values()]
    .map(record => ({ ...record, locations: record.locations.sort() }))
    .sort((left, right) => left.key.localeCompare(right.key));
  const english = Object.fromEntries(records.map(record => [record.key, record.source]));
  return {
    english,
    ledger: {
      version: 1,
      source_count: records.length,
      source_hash: hashText(JSON.stringify(english)),
      roots: {
        html: HTML_FILES,
        javascript: sourceFiles().map(relative),
        server: PYTHON_ROOTS,
        css: ['static/style.css'],
      },
      entries: records,
    },
  };
}

function extractCatalog({ check = false } = {}) {
  const snapshot = buildSourceSnapshot();
  if (check) {
    const ledger = readJson(path.join(I18N_DIR, 'ledger.json'), {});
    const committedEnglish = readJson(path.join(I18N_DIR, 'en.json'), {});
    if (
      ledger.source_hash !== snapshot.ledger.source_hash
      || ledger.source_count !== snapshot.ledger.source_count
      || JSON.stringify(ledger.entries) !== JSON.stringify(snapshot.ledger.entries)
      || JSON.stringify(committedEnglish) !== JSON.stringify(snapshot.english)
    ) {
      throw new Error(
        `catalog source snapshot is stale: expected ${snapshot.ledger.source_count}/${snapshot.ledger.source_hash}, `
        + `found ${ledger.source_count || 0}/${ledger.source_hash || 'missing'}; run extract`,
      );
    }
    process.stdout.write(`source snapshot current keys=${snapshot.ledger.source_count} hash=${snapshot.ledger.source_hash}\n`);
    return;
  }

  writeJson(path.join(I18N_DIR, 'en.json'), snapshot.english);
  for (const locale of Object.keys(LOCALES).filter(id => id !== 'en')) {
    const file = path.join(I18N_DIR, `${locale}.json`);
    const values = readJson(file, {});
    writeJson(file, Object.fromEntries(
      Object.keys(snapshot.english)
        .filter(key => typeof values[key] === 'string')
        .map(key => [key, values[key]]),
    ));
  }
  writeJson(path.join(I18N_DIR, 'ledger.json'), snapshot.ledger);
  writeJson(path.join(I18N_DIR, 'registry.json'), registry());
  writeJson(path.join(I18N_DIR, 'brands.json'), {
    brands: BRANDS,
    stable_tokens: STABLE_TOKENS,
  });
  process.stdout.write(`extracted=${snapshot.ledger.source_count} hash=${snapshot.ledger.source_hash}\n`);
}

function registry() {
  return {
    version: 1,
    source: STEAM_LANGUAGE_SOURCE,
    support_level: 'full-platform',
    default_locale: 'en',
    locales: Object.fromEntries(
      Object.entries(LOCALES).map(([id, meta]) => [id, { name: meta.name, dir: meta.dir }]),
    ),
    aliases: ALIASES,
  };
}

function unexpectedScripts(locale, source, target) {
  const allowed = LOCALE_SCRIPTS[locale] || new Set();
  const unexpected = [];
  for (const [script, pattern] of Object.entries(SCRIPT_PATTERNS)) {
    if (allowed.has(script)) continue;
    if ([...target].some(character => pattern.test(character) && !source.includes(character))) {
      unexpected.push(script);
    }
  }
  return unexpected;
}

function validate() {
  extractCatalog({ check: true });
  const english = readJson(path.join(I18N_DIR, 'en.json'));
  if (!english || Array.isArray(english)) throw new Error('missing or invalid en.json');
  const expectedKeys = Object.keys(english).sort();
  const errors = [];
  const warnings = [];
  const actualRegistry = readJson(path.join(I18N_DIR, 'registry.json'));
  if (JSON.stringify(actualRegistry) !== JSON.stringify(registry())) {
    errors.push('registry.json does not match the Steam full-platform locale contract');
  }
  for (const [locale, meta] of Object.entries(LOCALES)) {
    const values = readJson(path.join(I18N_DIR, `${locale}.json`));
    if (!values || Array.isArray(values)) {
      errors.push(`${locale}: missing or invalid catalog`);
      continue;
    }
    const keys = Object.keys(values).sort();
    const missing = expectedKeys.filter(key => !(key in values));
    const extra = keys.filter(key => !(key in english));
    if (missing.length) errors.push(`${locale}: ${missing.length} missing keys`);
    if (extra.length) errors.push(`${locale}: ${extra.length} extra keys`);
    for (const key of expectedKeys) {
      if (!(key in values)) continue;
      const validStructure = structurallyValid(english[key], values[key]);
      const unexpected = validStructure
        ? unexpectedScripts(locale, english[key], values[key])
        : [];
      if (!validStructure) {
        errors.push(`${locale}:${key}: structurally invalid`);
      } else if (unexpected.length) {
        errors.push(
          `${locale}:${key}: unexpected script `
          + unexpected.join(','),
        );
      } else if (
        locale !== 'en'
        && english[key] === values[key]
        && /[A-Za-z]{3}/.test(english[key])
        && !isCodeLiteral(english[key])
        && ![...BRANDS, ...STABLE_TOKENS].includes(english[key])
      ) {
        warnings.push(`${locale}:${key}: unchanged English`);
      }
    }
    process.stdout.write(
      `${locale}: keys=${keys.length}/${expectedKeys.length} dir=${meta.dir}\n`,
    );
  }
  for (const warning of warnings.slice(0, 30)) process.stderr.write(`warning: ${warning}\n`);
  if (warnings.length > 30) process.stderr.write(`warning: ... ${warnings.length - 30} more\n`);
  if (errors.length) {
    for (const error of errors.slice(0, 80)) process.stderr.write(`error: ${error}\n`);
    if (errors.length > 80) process.stderr.write(`error: ... ${errors.length - 80} more\n`);
    throw new Error(`catalog validation failed with ${errors.length} error(s)`);
  }
  process.stdout.write(`validated locales=${Object.keys(LOCALES).length} keys=${expectedKeys.length} warnings=${warnings.length}\n`);
}

function manifests() {
  const source = readJson(path.join(ROOT, 'static', 'manifest.json'));
  const english = readJson(path.join(I18N_DIR, 'en.json'));
  const descriptionKey = Object.keys(english).find(key => english[key] === source.description);
  for (const locale of Object.keys(LOCALES)) {
    const values = readJson(path.join(I18N_DIR, `${locale}.json`), {});
    writeJson(path.join(ROOT, 'static', `manifest.${locale}.json`), {
      ...source,
      lang: locale,
      description: values[descriptionKey] || source.description,
    });
  }
  process.stdout.write(`generated manifests=${Object.keys(LOCALES).length}\n`);
}

async function main() {
  const [command = 'validate'] = process.argv.slice(2);
  if (command === 'extract') extractCatalog();
  else if (command === 'check-sources') extractCatalog({ check: true });
  else if (command === 'validate') validate();
  else if (command === 'manifests') manifests();
  else {
    throw new Error('usage: i18n-catalog.mjs extract|check-sources|validate|manifests');
  }
}

export {
  BRANDS,
  STABLE_TOKENS,
  LOCALES,
  isCodeLiteral,
  structurallyValid,
  unexpectedScripts,
};

if (process.argv[1] === fileURLToPath(import.meta.url)) {
  main().catch(error => {
    process.stderr.write(`${error.stack || error.message}\n`);
    process.exitCode = 1;
  });
}
