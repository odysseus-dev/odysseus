import fs from 'node:fs';
import vm from 'node:vm';

// Execute the actual public rewrite controller with network/UI ports. Rendering
// and real routes are covered separately by the running-app browser check.
const input = JSON.parse(process.argv[2]);
const source = fs.readFileSync('static/js/chat.js', 'utf8');
const start = source.indexOf('export async function rewriteWith(');
const end = source.indexOf('\n  /**\n   * Continue the AI', start);
if (start < 0 || end < start) throw new Error('rewrite controller not found');
const helper = fs.readFileSync('static/js/chatStreamErrors.js', 'utf8').replace(/export function /g, 'function ');
const errors = [], requests = [];
let destroyed = 0, navigation = 0;
const body = { innerHTML: '<p>Original answer</p>', textContent: 'Original answer', appendChild() {} };
const message = { dataset: { raw: 'Original answer' }, querySelector: () => body };
const bytes = new TextEncoder().encode(input.stream || '');
const chunks = [];
const size = input.chunk_size || bytes.length || 1;
for (let i = 0; i < bytes.length; i += size) chunks.push(bytes.slice(i, i + size));
const context = vm.createContext({
  TextDecoder,
  console: { error() {} },
  sessionModule: { getCurrentSessionId: () => 'owned-session' },
  spinnerModule: { createWhirlpool: () => ({ element: { style: {} }, destroy() { destroyed++; } }) },
  markdownModule: { processWithThinking: t => t, squashOutsideCode: t => t, normalizeThinkingMarkup: t => t },
  uiModule: { showError: e => errors.push(e), scrollHistory() {} },
  API_BASE: '',
  _renderVariantNav() { navigation++; },
  fetch: async (url, options) => {
    requests.push({ url, body: JSON.parse(options.body) });
    if (url !== '/api/rewrite') return { ok: true };
    let index = 0;
    return { ok: !input.http_status, status: input.http_status || 200,
      body: { getReader: () => ({ read: async () => index < chunks.length
        ? { done: false, value: chunks[index++] } : { done: true } }) } };
  },
});
vm.runInContext(helper + '\n' + source.slice(start, end).replace('export async function', 'async function'), context);
await context.rewriteWith(message, 'Rewrite shorter');
console.log(JSON.stringify({ errors, requests, raw: message.dataset.raw, html: body.innerHTML, variants: message.dataset.variants || null, destroyed, navigation }));
