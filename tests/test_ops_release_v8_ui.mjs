import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const src = fs.readFileSync('static/js/tasks.js', 'utf8');
const sanitized = src
  .replace(/^import\s[\s\S]*?;\s*$/gm, '')
  .replace(/^export\s+default\s+.*?;\s*$/gm, '')
  .replace(/^export\s+/gm, '');

const elements = new Map();
const openedUrls = [];
const copiedIds = [];
const toasts = [];

function makeEl(id) {
  const el = {
    id,
    style: {},
    dataset: {},
    innerHTML: '',
    textContent: '',
    addEventListener() {},
    querySelector() { return null; },
    querySelectorAll() { return []; },
  };
  elements.set(id, el);
  return el;
}

makeEl('tasks-pending-approvals');
const historyHost = makeEl('tasks-approval-history');

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
  confirm: () => true,
  navigator: {
    clipboard: {
      async writeText(txt) { copiedIds.push(txt); },
    },
  },
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
    showToast(msg, kind) { toasts.push({ msg, kind }); },
    showError(msg) { throw new Error('Unexpected UI error: ' + msg); },
  },
  fetch: async () => ({ ok: true, json: async () => ({ decisions: [], count: 0 }) }),
};

vm.createContext(sandbox);
vm.runInContext(sanitized, sandbox);

vm.runInContext("_approvalDecisionHistory = [{ approval_id: 'ap-v8-1', tool_name: 'send_email', decision: 'deny', digest: '99aabbcc', resolved_at: '2026-11-13T10:15:00Z', external_untrusted_context_seen: true }, { approval_id: 'ap-v8-2', tool_name: 'write_file', decision: 'approve_task', digest: '11223344', resolved_at: '2026-11-13T10:14:00Z', external_untrusted_context_seen: false }]; _approvalHistoryFilter = 'deny'; _approvalHistoryToolFilter = 'send_email';", sandbox);
sandbox._renderApprovalHistory();

assert.ok(historyHost.innerHTML.includes('id="tasks-approval-history-export-json"'));
assert.ok(!historyHost.innerHTML.includes('id="tasks-approval-history-detail"'));

// Expand detail drawer for ap-v8-1
sandbox._toggleApprovalHistoryDetail('ap-v8-1');
assert.ok(historyHost.innerHTML.includes('id="tasks-approval-history-detail"'));
assert.ok(historyHost.innerHTML.includes('ap-v8-1'));
assert.ok(historyHost.innerHTML.includes('External Untrusted Context'));
assert.ok(historyHost.innerHTML.includes('id="tasks-approval-history-copy-id"'));

// Copy ID copies approval_id to clipboard and triggers info toast
const copied = await sandbox._copyApprovalHistoryId('ap-v8-1');
assert.equal(copied, true);
assert.deepEqual(copiedIds, ['ap-v8-1']);
assert.equal(toasts.length, 1);

// JSON export preserves active decision and tool_name filters
const jsonUrl = sandbox._exportApprovalHistory('json');
assert.equal(jsonUrl, '/api/tasks/approvals/history/export?format=json&limit=200&decision=deny&tool_name=send_email');
assert.equal(openedUrls[0], jsonUrl);

// Toggle same ID collapses detail drawer
sandbox._toggleApprovalHistoryDetail('ap-v8-1');
assert.ok(!historyHost.innerHTML.includes('id="tasks-approval-history-detail"'));

const appJs = fs.readFileSync('static/app.js', 'utf8');
const swJs = fs.readFileSync('static/sw.js', 'utf8');
const runbook = fs.readFileSync('docs/OPS_RELEASE_V8.md', 'utf8');
assert.ok(appJs.includes('./js/tasks.js?v=20261113opsreleasev8'));
assert.ok(swJs.includes('/static/js/tasks.js?v=20261113opsreleasev8'));
assert.ok(swJs.includes('odysseus-v1140-ops-release-v8'));
assert.ok(runbook.includes('tasks.js?v=20261113opsreleasev8'));

console.log('PASS: ops-release-v8 expandable audit detail drawer, Copy ID, JSON export & cache sync tests');