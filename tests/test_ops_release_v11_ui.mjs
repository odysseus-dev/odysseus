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
makeEl('tasks-approval-history');

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
  setInterval() { return 1; },
  clearInterval() {},
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
      if (!docListeners.has(type)) docListeners.set(type, []);
      docListeners.get(type).push(fn);
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

// 1. 3-way composable filtering (tool + risk + preview/digest search) & Clear Filters chip
vm.runInContext(`
  _pendingApprovals = [
    { approval_id: 's-1', tool_name: 'write_file', digest: 'aa112233', content_preview: 'Deploy prod config', external_untrusted_context_seen: false },
    { approval_id: 's-2', tool_name: 'write_file', digest: 'bb445566', content_preview: 'Update staging docs', external_untrusted_context_seen: false },
    { approval_id: 's-3', tool_name: 'write_file', digest: 'cc778899', content_preview: 'Deploy prod untrusted', external_untrusted_context_seen: true },
    { approval_id: 's-4', tool_name: 'send_email', digest: 'dd001122', content_preview: 'Deploy prod notice', external_untrusted_context_seen: false },
  ];
  _selectedApprovalIds = new Set();
  _pendingApprovalsToolFilter = 'write_file';
  _pendingApprovalsRiskFilter = 'safe';
  _pendingApprovalsSearch = 'prod';
  _renderPendingApprovals();
`, sandbox);

assert.ok(pendingHost.innerHTML.includes('id="tasks-approvals-search"'));
assert.ok(pendingHost.innerHTML.includes('id="tasks-approvals-clear-filters"'));
assert.ok(pendingHost.innerHTML.includes('Pending Approvals (1/4)'));
assert.ok(pendingHost.innerHTML.includes('data-approval-id="s-1"'));
assert.ok(!pendingHost.innerHTML.includes('data-approval-id="s-2"'));
assert.ok(!pendingHost.innerHTML.includes('data-approval-id="s-3"'));
assert.ok(!pendingHost.innerHTML.includes('data-approval-id="s-4"'));

// 2. Keyboard batch selection shortcuts (Alt+A and Alt+S)
vm.runInContext(`
  _pendingApprovalsToolFilter = '';
  _pendingApprovalsRiskFilter = '';
  _pendingApprovalsSearch = '';
  _selectedApprovalIds = new Set();
  _renderPendingApprovals();
`, sandbox);

const keydownHandlers = docListeners.get('keydown') || [];
assert.ok(keydownHandlers.length > 0, 'Expected keydown listener');

// Alt+A selects all 4 visible approvals
keydownHandlers.forEach(fn => fn({ altKey: true, key: 'a', target: { tagName: 'DIV' }, preventDefault() {} }));
assert.deepEqual(Array.from(vm.runInContext('[..._selectedApprovalIds]', sandbox)), ['s-1', 's-2', 's-3', 's-4']);

// Alt+S narrows selection to safe approvals only (excludes external untrusted s-3)
keydownHandlers.forEach(fn => fn({ altKey: true, key: 's', target: { tagName: 'DIV' }, preventDefault() {} }));
assert.deepEqual(Array.from(vm.runInContext('[..._selectedApprovalIds]', sandbox)), ['s-1', 's-2', 's-4']);

// Input target guard prevents shortcut firing while typing in search input
keydownHandlers.forEach(fn => fn({ altKey: true, key: 'a', target: { tagName: 'INPUT' }, preventDefault() {} }));
assert.deepEqual(Array.from(vm.runInContext('[..._selectedApprovalIds]', sandbox)), ['s-1', 's-2', 's-4']);

const adminJs = fs.readFileSync('static/js/admin.js', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
const swJs = fs.readFileSync('static/sw.js', 'utf8');
const runbook = fs.readFileSync('docs/OPS_RELEASE_V11.md', 'utf8');
assert.ok(adminJs.includes('admin-ops-run-status-filter'));
assert.ok(adminJs.includes('function _filterOpsEffectRuns'));
assert.ok(appJs.includes('./js/tasks.js?v=20261113opsreleasev11'));
assert.ok(appJs.includes('./js/admin.js?v=20261113opsreleasev11'));
assert.ok(swJs.includes('/static/js/tasks.js?v=20261113opsreleasev11'));
assert.ok(swJs.includes('/static/js/admin.js?v=20261113opsreleasev11'));
assert.ok(swJs.includes('odysseus-v1143-ops-release-v11'));
assert.ok(runbook.includes('tasks.js?v=20261113opsreleasev11'));

console.log('PASS: ops-release-v11 pending queue preview search, Clear Filters, Alt+A/Alt+S shortcuts, Admin Ops run filter & cache sync tests');