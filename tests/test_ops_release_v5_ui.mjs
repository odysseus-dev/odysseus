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
    addEventListener(type, fn) { listeners.set(type, fn); },
    querySelector(sel) {
      if (sel === '#tasks-approvals-refresh') return makeEl('tasks-approvals-refresh');
      if (sel === '#tasks-approval-history-export') return makeEl('tasks-approval-history-export');
      return null;
    },
    querySelectorAll() { return []; },
  };
  elements.set(id, el);
  return el;
}

const pendingHost = makeEl('tasks-pending-approvals');
const historyHost = makeEl('tasks-approval-history');

const openedUrls = [];
const toasts = [];
let confirmResult = false; // User cancels -> skip external untrusted items
let pendingList = [
  { approval_id: 'ap-safe', tool_name: 'write_file', digest: '1111aaaa', content_preview: 'Safe', external_untrusted_context_seen: false, expires_at: Math.floor(Date.now() / 1000) + 120 },
  { approval_id: 'ap-ext', tool_name: 'send_email', digest: '2222bbbb', content_preview: 'Ext', external_untrusted_context_seen: true, expires_at: Math.floor(Date.now() / 1000) + 120 },
];
let historyList = [];

const sandbox = {
  console,
  Date,
  Math,
  Number,
  String,
  Array,
  JSON,
  URLSearchParams,
  encodeURIComponent,
  setInterval: () => 1,
  clearInterval: () => {},
  confirm: () => confirmResult,
  window: {
    location: { origin: '' },
    open(url) { openedUrls.push(url); },
  },
  document: {
    hidden: false,
    addEventListener() {},
    createElement() {
      let _t = '';
      return {
        set textContent(v) { _t = String(v ?? ''); },
        get innerHTML() {
          return _t
            .replace(/&/g, '&amp;')
            .replace(/</g, '&lt;')
            .replace(/>/g, '&gt;')
            .replace(/"/g, '&quot;');
        },
      };
    },
    getElementById(id) { return elements.get(id) || null; },
  },
  uiModule: {
    showToast(msg) { toasts.push(msg); },
    showError(msg) { throw new Error('Unexpected UI error: ' + msg); },
  },
  fetch: async (url, opts = {}) => {
    if (url === '/api/tasks/approvals/pending') {
      return { ok: true, json: async () => ({ approvals: pendingList, count: pendingList.length }) };
    }
    if (url === '/api/tasks/approvals/resolve-bulk') {
      const body = JSON.parse(opts.body);
      assert.equal(body.decision, 'approve_task');
      assert.equal(body.skip_external_untrusted, true);
      pendingList = pendingList.filter(p => p.approval_id === 'ap-ext');
      historyList = [
        { approval_id: 'ap-safe', tool_name: 'write_file', digest: '1111aaaa', decision: 'approve_task', resolved_at: Math.floor(Date.now() / 1000) },
      ];
      return {
        ok: true,
        json: async () => ({
          ok: true,
          decision: 'approve_task',
          resolved_count: 1,
          failed: [{ approval_id: 'ap-ext', reason: 'external_untrusted_context' }],
        }),
      };
    }
    if (url.startsWith('/api/tasks/approvals/history?')) {
      return { ok: true, json: async () => ({ decisions: historyList, count: historyList.length }) };
    }
    throw new Error('Unhandled URL: ' + url);
  },
};

vm.createContext(sandbox);
vm.runInContext(sanitized, sandbox);

await sandbox._fetchPendingApprovals();
await sandbox._resolveAllPendingApprovals('approve_task');

assert.equal(pendingList.length, 1);
assert.equal(pendingList[0].approval_id, 'ap-ext');
assert.ok(toasts.some(t => t.includes('Approved 1 pending action (1 external skipped)')));
assert.ok(historyHost.innerHTML.includes('id="tasks-approval-history-export"'));
assert.ok(historyHost.innerHTML.includes('data-history-filter="deny"'));

sandbox._approvalHistoryFilter = 'deny';
const exportUrl = sandbox._exportApprovalHistoryCsv();
assert.equal(exportUrl, '/api/tasks/approvals/history/export?format=csv&limit=200&decision=deny');
assert.equal(openedUrls[0], exportUrl);
console.log('PASS: ops-release-v5 external bulk guard, decision filters & CSV export UI tests');