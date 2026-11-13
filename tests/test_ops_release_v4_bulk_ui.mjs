import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const src = fs.readFileSync('static/js/tasks.js', 'utf8');
const sanitized = src.replace(/^import\s[\s\S]*?;\s*$/gm, '').replace(/^export\s+default\s+.*?;\s*$/gm, '').replace(/^export\s+/gm, '');

const elements = new Map();
function makeEl(id) {
  const listeners = new Map();
  const el = {
    id,
    style: {},
    dataset: {},
    innerHTML: '',
    textContent: '',
    disabled: false,
    listeners,
    addEventListener(type, fn) {
      listeners.set(type, fn);
    },
    querySelector(sel) {
      if (sel === '#tasks-approvals-refresh') return makeEl('tasks-approvals-refresh');
      return null;
    },
    querySelectorAll() {
      return [];
    },
  };
  elements.set(id, el);
  return el;
}

const pendingHost = makeEl('tasks-pending-approvals');
const historyHost = makeEl('tasks-approval-history');

const fetchCalls = [];
const toasts = [];
let pendingList = [
  { approval_id: 'ap-1', tool_name: 'send_email', digest: '1111aaaa', content_preview: 'Email A', expires_at: Math.floor(Date.now() / 1000) + 120 },
  { approval_id: 'ap-2', tool_name: 'write_file', digest: '2222bbbb', content_preview: 'Write B', expires_at: Math.floor(Date.now() / 1000) + 120 },
];
let historyList = [];

const sandbox = {
  window: { location: { origin: '' } },
  console,
  Date,
  Math,
  Number,
  String,
  Array,
  JSON,
  encodeURIComponent,
  setInterval: () => 1,
  clearInterval: () => {},
  document: {
    hidden: false,
    addEventListener() {},
    getElementById(id) {
      return elements.get(id) || null;
    },
  },
  uiModule: {
    showToast(msg) { toasts.push(msg); },
    showError(msg) { throw new Error('Unexpected UI error: ' + msg); },
  },
  fetch: async (url, opts = {}) => {
    fetchCalls.push({ url, opts });
    if (url === '/api/tasks/approvals/pending') {
      return { ok: true, json: async () => ({ approvals: pendingList, count: pendingList.length }) };
    }
    if (url === '/api/tasks/approvals/resolve-bulk') {
      const body = JSON.parse(opts.body);
      assert.deepEqual(body.approval_ids, ['ap-1', 'ap-2']);
      assert.equal(body.decision, 'deny');
      pendingList = [];
      historyList = [
        { approval_id: 'ap-2', tool_name: 'write_file', digest: '2222bbbb', decision: 'deny', resolved_at: Math.floor(Date.now() / 1000) },
        { approval_id: 'ap-1', tool_name: 'send_email', digest: '1111aaaa', decision: 'deny', resolved_at: Math.floor(Date.now() / 1000) },
      ];
      return { ok: true, json: async () => ({ ok: true, decision: 'deny', resolved_count: 2, failed: [] }) };
    }
    if (url.startsWith('/api/tasks/approvals/history')) {
      return { ok: true, json: async () => ({ decisions: historyList, count: historyList.length }) };
    }
    throw new Error('Unhandled URL: ' + url);
  },
};

vm.createContext(sandbox);
vm.runInContext(sanitized, sandbox);

await sandbox._fetchPendingApprovals();
assert.equal(pendingHost.style.display, 'flex');
assert.ok(pendingHost.innerHTML.includes('id="tasks-approvals-approve-all"'));
assert.ok(pendingHost.innerHTML.includes('id="tasks-approvals-deny-all"'));
assert.equal(historyHost.style.display, 'none');

await sandbox._resolveAllPendingApprovals('deny');
assert.equal(pendingHost.style.display, 'none');
assert.equal(historyHost.style.display, 'flex');
assert.ok(historyHost.innerHTML.includes('Recent Decisions (2)'));
assert.ok(historyHost.innerHTML.includes('write_file'));
assert.ok(historyHost.innerHTML.includes('send_email'));
assert.ok(toasts.some(t => t.includes('Denied 2 pending actions')));
console.log('PASS: ops-release-v4 bulk approval & decision history UI tests');