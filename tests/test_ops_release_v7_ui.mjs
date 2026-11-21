import assert from 'node:assert/strict';
import fs from 'node:fs';
import vm from 'node:vm';

const src = fs.readFileSync('static/js/tasks.js', 'utf8');
const sanitized = src
  .replace(/^import\s[\s\S]*?;\s*$/gm, '')
  .replace(/^export\s+default\s+.*?;\s*$/gm, '')
  .replace(/^export\s+/gm, '');

const elements = new Map();
let selectAllEl = null;

function makeEl(id) {
  const listeners = new Map();
  const el = {
    id,
    style: {},
    dataset: {},
    innerHTML: '',
    textContent: '',
    disabled: false,
    checked: false,
    indeterminate: false,
    listeners,
    addEventListener(type, fn) { listeners.set(type, fn); },
    querySelector(sel) {
      if (sel === '#tasks-approvals-select-all') {
        selectAllEl = makeEl('tasks-approvals-select-all');
        const m = el.innerHTML.match(/<input[^>]*id="tasks-approvals-select-all"[^>]*>/); selectAllEl.checked = Boolean(m && m[0].includes(' checked'));
        return selectAllEl;
      }
      if (sel === '#tasks-approvals-select-safe') return makeEl('tasks-approvals-select-safe');
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
makeEl('tasks-approval-history');

let pendingList = [
  { approval_id: 'ap-1', tool_name: 'write_file', digest: '1111aaaa', content_preview: 'Safe 1', external_untrusted_context_seen: false, expires_at: Math.floor(Date.now() / 1000) + 120 },
  { approval_id: 'ap-2', tool_name: 'send_email', digest: '2222bbbb', content_preview: 'Ext 2', external_untrusted_context_seen: true, expires_at: Math.floor(Date.now() / 1000) + 120 },
  { approval_id: 'ap-3', tool_name: 'read_file', digest: '3333cccc', content_preview: 'Safe 3', external_untrusted_context_seen: false, expires_at: Math.floor(Date.now() / 1000) + 120 },
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
  confirm: () => true,
  window: { location: { origin: '' }, open() {} },
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
  fetch: async (url) => {
    if (url === '/api/tasks/approvals/pending') {
      return { ok: true, json: async () => ({ approvals: pendingList, count: pendingList.length }) };
    }
    if (url.startsWith('/api/tasks/approvals/history?')) {
      return { ok: true, json: async () => ({ decisions: [], count: 0 }) };
    }
    throw new Error('Unhandled URL: ' + url);
  },
};

vm.createContext(sandbox);
vm.runInContext(sanitized, sandbox);

await sandbox._fetchPendingApprovals();
assert.ok(pendingHost.innerHTML.includes('id="tasks-approvals-select-all"'));
assert.ok(pendingHost.innerHTML.includes('id="tasks-approvals-select-safe"'));
assert.equal(selectAllEl.checked, true);
assert.equal(selectAllEl.indeterminate, false);

// Select Safe Only unchecks external-context item ap-2 and sets indeterminate state
sandbox._selectSafePendingApprovals();
assert.equal(selectAllEl.checked, false);
assert.equal(selectAllEl.indeterminate, true);
assert.ok(pendingHost.innerHTML.includes('Approve Selected (2)'));

// Master Select All clears and restores all items
sandbox._selectAllPendingApprovals(false);
assert.equal(selectAllEl.checked, false);
assert.equal(selectAllEl.indeterminate, false);
assert.ok(pendingHost.innerHTML.includes('id="tasks-approvals-approve-all" class="memory-toolbar-btn" data-bulk-approval-action="approve_task" style="padding:2px 6px;font-size:10px;" disabled'));

sandbox._selectAllPendingApprovals(true);
assert.equal(selectAllEl.checked, true);
assert.equal(selectAllEl.indeterminate, false);
assert.ok(pendingHost.innerHTML.includes('Approve All'));

const historyHost = elements.get('tasks-approval-history');
vm.runInContext("_approvalDecisionHistory = [{ approval_id: 'h-1', tool_name: 'write_file', decision: 'approve_task', digest: 'aaaa1111' }, { approval_id: 'h-2', tool_name: 'send_email', decision: 'deny', digest: 'bbbb2222' }, { approval_id: 'h-3', tool_name: 'read_file', decision: 'approve_task', digest: 'cccc3333' }]; _approvalHistoryFilter = 'approve'; _approvalHistoryToolFilter = 'write_file';", sandbox);
sandbox._renderApprovalHistory();

assert.ok(historyHost.innerHTML.includes('Approved: 2 · Denied: 1'));
assert.ok(historyHost.innerHTML.includes('id="tasks-approval-history-reset"'));

const appJs = fs.readFileSync('static/app.js', 'utf8');
const swJs = fs.readFileSync('static/sw.js', 'utf8');
const runbook = fs.readFileSync('docs/OPS_RELEASE_V7.md', 'utf8');
assert.ok(appJs.includes('./js/tasks.js?v=20261113opsreleasev12'));
assert.ok(swJs.includes('/static/js/tasks.js?v=20261113opsreleasev12'));
assert.ok(swJs.includes('odysseus-v1144-ops-release-v12'));
assert.ok(runbook.includes('tasks.js?v=20261113opsreleasev7'));

console.log('PASS: ops-release-v7 Select All, Select Safe Only, history counters & asset cache sync tests');