import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const src = fs.readFileSync('static/js/tasks.js', 'utf8');
const sanitized = src
  .replace(/^import\s[\s\S]*?;\s*$/gm, '')
  .replace(/^export\s+default\s+.*?;\s*$/gm, '')
  .replace(/^export\s+/gm, '');

const elements = new Map();
const intervals = [];
let fetchCalls = 0;

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
  setInterval(fn, ms) {
    intervals.push({ fn, ms });
    return intervals.length;
  },
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
    addEventListener() {},
    getElementById(id) { return elements.get(id) || null; },
  },
  uiModule: {
    showToast() {},
    showError(msg) { throw new Error('Unexpected UI error: ' + msg); },
  },
  fetch: async () => {
    fetchCalls += 1;
    return { ok: true, json: async () => ({ approvals: [], decisions: [] }) };
  },
};

vm.createContext(sandbox);
vm.runInContext(sanitized, sandbox);

// 1. Composable tool + risk-context filter & scoped Select All
vm.runInContext(`
  _pendingApprovals = [
    { approval_id: 'r-1', tool_name: 'write_file', digest: '1111aaaa', content_preview: 'safe write', external_untrusted_context_seen: false },
    { approval_id: 'r-2', tool_name: 'write_file', digest: '2222bbbb', content_preview: 'external write', external_untrusted_context_seen: true },
    { approval_id: 'r-3', tool_name: 'send_email', digest: '3333cccc', content_preview: 'safe email', external_untrusted_context_seen: false },
  ];
  _selectedApprovalIds = new Set();
  _pendingApprovalsToolFilter = 'write_file';
  _pendingApprovalsRiskFilter = 'safe';
  _renderPendingApprovals();
`, sandbox);

assert.ok(pendingHost.innerHTML.includes('id="tasks-approvals-risk-filter"'));
assert.ok(pendingHost.innerHTML.includes('Pending Approvals (1/3)'));
assert.ok(pendingHost.innerHTML.includes('data-approval-id="r-1"'));
assert.ok(!pendingHost.innerHTML.includes('data-approval-id="r-2"'));
assert.ok(!pendingHost.innerHTML.includes('data-approval-id="r-3"'));

sandbox._selectAllPendingApprovals(true);
const selectedScoped = Array.from(vm.runInContext('[..._selectedApprovalIds]', sandbox));
assert.deepEqual(selectedScoped, ['r-1']);

// 2. Polling pause/resume suppresses background poll ticks
assert.ok(pendingHost.innerHTML.includes('id="tasks-approvals-pause-toggle"'));
assert.ok(pendingHost.innerHTML.includes('Pause Poll'));
const pollEntry = intervals.find(entry => entry.ms === 10000);
assert.ok(pollEntry, 'Expected 10s poll timer');

vm.runInContext('_approvalsPollingPaused = true; _renderPendingApprovals();', sandbox);
assert.ok(pendingHost.innerHTML.includes('Resume Poll'));
const beforePausedTick = fetchCalls;
pollEntry.fn();
assert.equal(fetchCalls, beforePausedTick);

vm.runInContext('_approvalsPollingPaused = false;', sandbox);
pollEntry.fn();
assert.equal(fetchCalls, beforePausedTick + 1);

// 3. Recent Decisions approval_id / digest search
vm.runInContext(`
  _approvalDecisionHistory = [
    { approval_id: 'apr-alpha-101', tool_name: 'write_file', decision: 'approve_task', digest: 'deadbeef' },
    { approval_id: 'apr-beta-202', tool_name: 'send_email', decision: 'deny', digest: 'cafebabe' },
  ];
  _approvalHistorySearch = 'cafebabe';
  _renderApprovalHistory();
`, sandbox);

assert.ok(historyHost.innerHTML.includes('id="tasks-approval-history-search"'));
assert.ok(historyHost.innerHTML.includes('Recent Decisions (1)'));
assert.ok(historyHost.innerHTML.includes('Approved: 0 · Denied: 1'));
assert.ok(historyHost.innerHTML.includes('data-approval-id="apr-beta-202"'));
assert.ok(!historyHost.innerHTML.includes('data-approval-id="apr-alpha-101"'));
assert.ok(historyHost.innerHTML.includes('id="tasks-approval-history-reset"'));

const adminJs = fs.readFileSync('static/js/admin.js', 'utf8');
const appJs = fs.readFileSync('static/app.js', 'utf8');
const swJs = fs.readFileSync('static/sw.js', 'utf8');
const runbook = fs.readFileSync('docs/OPS_RELEASE_V10.md', 'utf8');
assert.ok(adminJs.includes('admin-ops-jump-unsettled'));
assert.ok(adminJs.includes('effects.unsettled_runs'));
assert.ok(appJs.includes('./js/tasks.js?v=20261113opsreleasev12'));
assert.ok(appJs.includes('./js/admin.js?v=20261113opsreleasev12'));
assert.ok(swJs.includes('/static/js/tasks.js?v=20261113opsreleasev12'));
assert.ok(swJs.includes('/static/js/admin.js?v=20261113opsreleasev12'));
assert.ok(swJs.includes('odysseus-v1144-ops-release-v12'));
assert.ok(runbook.includes('tasks.js?v=20261113opsreleasev10'));

console.log('PASS: ops-release-v10 risk-context filter, polling pause/resume, audit search, Admin Ops jump & cache sync tests');