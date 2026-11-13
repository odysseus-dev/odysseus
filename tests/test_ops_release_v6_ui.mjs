import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const src = fs.readFileSync('static/js/tasks.js', 'utf8');
const sanitized = src
  .replace(/^import\s[\s\S]*?;\s*$/gm, '')
  .replace(/^export\s+default\s+.*?;\s*$/gm, '')
  .replace(/^export\s+/gm, '');

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
      if (sel === '#tasks-approval-history-tool') return null;
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
const historyUrls = [];
const toasts = [];
let confirmCalls = 0;

let pendingList = [
  { approval_id: 'ap-1', tool_name: 'write_file', digest: '1111aaaa', content_preview: 'Safe 1', external_untrusted_context_seen: false, expires_at: Math.floor(Date.now() / 1000) + 120 },
  { approval_id: 'ap-2', tool_name: 'send_email', digest: '2222bbbb', content_preview: 'Ext 2', external_untrusted_context_seen: true, expires_at: Math.floor(Date.now() / 1000) + 120 },
  { approval_id: 'ap-3', tool_name: 'read_file', digest: '3333cccc', content_preview: 'Safe 3', external_untrusted_context_seen: false, expires_at: Math.floor(Date.now() / 1000) + 120 },
];
let historyList = [
  { approval_id: 'ap-old-1', tool_name: 'write_file', digest: '9999ffff', decision: 'approve_task', resolved_at: Math.floor(Date.now() / 1000) },
  { approval_id: 'ap-old-2', tool_name: 'send_email', digest: '8888eeee', decision: 'deny', resolved_at: Math.floor(Date.now() / 1000) },
];

const sandbox = {
  console,
  Date,
  Math,
  Number,
  String,
  Array,
  Set,
  JSON,
  URLSearchParams,
  encodeURIComponent,
  setInterval: () => 1,
  clearInterval: () => {},
  confirm: () => { confirmCalls += 1; return true; },
  window: {
    location: { origin: '' },
    open(url) { openedUrls.push(url); },
  },
  document: {
    hidden: false,
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
    addEventListener() {},
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
      // Only ap-1 and ap-3 should be submitted when ap-2 is deselected
      assert.deepEqual(body.approval_ids, ['ap-1', 'ap-3']);
      assert.equal(body.decision, 'approve_task');
      assert.equal(body.skip_external_untrusted, false);
      pendingList = pendingList.filter(p => p.approval_id === 'ap-2');
      return { ok: true, json: async () => ({ ok: true, decision: 'approve_task', resolved_count: 2, failed: [] }) };
    }
    if (url.startsWith('/api/tasks/approvals/history?')) {
      historyUrls.push(url);
      return { ok: true, json: async () => ({ decisions: historyList, count: historyList.length }) };
    }
    throw new Error('Unhandled URL: ' + url);
  },
};

vm.createContext(sandbox);
vm.runInContext(sanitized, sandbox);

await sandbox._fetchPendingApprovals();
assert.ok(pendingHost.innerHTML.includes('Approve All'));
assert.ok(pendingHost.innerHTML.includes('class="task-approval-select"'));

// Deselect ap-2 (the external untrusted item) -> button updates to Approve Selected (2) and no confirm prompt fires
vm.runInContext("_selectedApprovalIds.delete('ap-2')", sandbox);
sandbox._renderPendingApprovals();
assert.ok(pendingHost.innerHTML.includes('Approve Selected (2)'));
assert.ok(pendingHost.innerHTML.includes('Deny Selected (2)'));

await sandbox._resolveAllPendingApprovals('approve_task');
assert.equal(confirmCalls, 0);
assert.equal(pendingList.length, 1);
assert.equal(pendingList[0].approval_id, 'ap-2');

// Tool-name dropdown rendered and wired into history + CSV export URLs
assert.ok(historyHost.innerHTML.includes('id="tasks-approval-history-tool"'));
assert.ok(historyHost.innerHTML.includes('value="send_email"'));

vm.runInContext("_approvalHistoryToolFilter = 'send_email'; _approvalHistoryFilter = 'deny';", sandbox);
await sandbox._fetchApprovalHistory();
assert.ok(historyUrls.at(-1).includes('decision=deny'));
assert.ok(historyUrls.at(-1).includes('tool_name=send_email'));

const exportUrl = sandbox._exportApprovalHistoryCsv();
assert.equal(exportUrl, '/api/tasks/approvals/history/export?format=csv&limit=200&decision=deny&tool_name=send_email');
assert.equal(openedUrls[0], exportUrl);

const appJs = fs.readFileSync('static/app.js', 'utf8');
const swJs = fs.readFileSync('static/sw.js', 'utf8');
const runbook = fs.readFileSync('docs/OPS_RELEASE_V6.md', 'utf8');
assert.ok(appJs.includes('./js/tasks.js?v=20261113opsreleasev12'));
assert.ok(swJs.includes('/static/js/tasks.js?v=20261113opsreleasev12'));
assert.ok(swJs.includes('odysseus-v1144-ops-release-v12'));
assert.ok(runbook.includes('/api/tasks/approvals/history/export'));

console.log('PASS: ops-release-v6 selective batch checkboxes, tool-name filter & asset cache sync tests');