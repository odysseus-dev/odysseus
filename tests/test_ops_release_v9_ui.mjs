import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const src = fs.readFileSync('static/js/tasks.js', 'utf8');
const sanitized = src
  .replace(/^import\s[\s\S]*?;\s*$/gm, '')
  .replace(/^export\s+default\s+.*?;\s*$/gm, '')
  .replace(/^export\s+/gm, '');

const elements = new Map();
const docListeners = new Map();

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

const pendingHost = makeEl('tasks-pending-approvals');
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
  window: { location: { origin: '' } },
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
    addEventListener(type, fn) {
      const list = docListeners.get(type) || [];
      list.push(fn);
      docListeners.set(type, list);
    },
    getElementById(id) { return elements.get(id) || null; },
  },
  uiModule: {
    showToast() {},
    showError(msg) { throw new Error('Unexpected UI error: ' + msg); },
  },
  fetch: async () => ({ ok: true, json: async () => ({ approvals: [], decisions: [] }) }),
};

vm.createContext(sandbox);
vm.runInContext(sanitized, sandbox);

// 1. Pending queue tool filter & scoped Select All
vm.runInContext(`
  _pendingApprovals = [
    { approval_id: 'p-1', tool_name: 'write_file', digest: '1111aaaa', content_preview: 'write a.txt', external_untrusted_context_seen: false },
    { approval_id: 'p-2', tool_name: 'write_file', digest: '2222bbbb', content_preview: 'write b.txt', external_untrusted_context_seen: false },
    { approval_id: 'p-3', tool_name: 'send_email', digest: '3333cccc', content_preview: 'send mail', external_untrusted_context_seen: true },
  ];
  _selectedApprovalIds = new Set();
  _pendingApprovalsToolFilter = 'write_file';
  _renderPendingApprovals();
`, sandbox);

assert.ok(pendingHost.innerHTML.includes('id="tasks-approvals-tool-filter"'));
assert.ok(pendingHost.innerHTML.includes('Pending Approvals (2/3)'));
assert.ok(!pendingHost.innerHTML.includes('data-approval-id="p-3"'));

sandbox._selectAllPendingApprovals(true);
const selectedAfterScopedAll = vm.runInContext('[..._selectedApprovalIds]', sandbox);
assert.deepEqual(Array.from(selectedAfterScopedAll), ['p-1', 'p-2']);
assert.ok(pendingHost.innerHTML.includes('Approve Selected (2)'));

// 2. Escape key dismisses open audit detail drawer
vm.runInContext(`
  _approvalDecisionHistory = [
    { approval_id: 'h-v9-1', tool_name: 'write_file', decision: 'approve_task', digest: 'abcd1234', resolved_at: '2026-11-13T10:30:00Z' }
  ];
  _renderApprovalHistory();
`, sandbox);

sandbox._toggleApprovalHistoryDetail('h-v9-1');
assert.ok(historyHost.innerHTML.includes('id="tasks-approval-history-detail"'));

for (const fn of (docListeners.get('keydown') || [])) {
  fn({ key: 'Escape' });
}
assert.ok(!historyHost.innerHTML.includes('id="tasks-approval-history-detail"'));

const adminJs = fs.readFileSync('static/js/admin.js', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
const swJs = fs.readFileSync('static/sw.js', 'utf8');
const runbook = fs.readFileSync('docs/OPS_RELEASE_V9.md', 'utf8');
assert.ok(adminJs.includes('admin-ops-open-approvals'));
assert.ok(adminJs.includes('external_untrusted_count'));
assert.ok(appJs.includes('./js/tasks.js?v=20261113opsreleasev11'));
assert.ok(appJs.includes('./js/admin.js?v=20261113opsreleasev11'));
assert.ok(swJs.includes('/static/js/tasks.js?v=20261113opsreleasev11'));
assert.ok(swJs.includes('/static/js/admin.js?v=20261113opsreleasev11'));
assert.ok(swJs.includes('odysseus-v1143-ops-release-v11'));
assert.ok(runbook.includes('tasks.js?v=20261113opsreleasev9'));

console.log('PASS: ops-release-v9 pending queue tool filter, keyboard drawer, Admin Ops cross-link & cache sync tests');