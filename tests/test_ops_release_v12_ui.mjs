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
    addEventListener() {},
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

// 1. Expiring soonest sort & <60s urgency badge
vm.runInContext(`
  const nowSec = Math.floor(Date.now() / 1000);
  _pendingApprovals = [
    { approval_id: 'later-1', tool_name: 'write_file', digest: '1111aaaa', content_preview: 'Later task', expires_at: nowSec + 300 },
    { approval_id: 'urgent-1', tool_name: 'send_email', digest: '2222bbbb', content_preview: 'Urgent task', expires_at: nowSec + 30 },
  ];
  _pendingApprovalsSort = 'expiring';
  _renderPendingApprovals();
`, sandbox);

assert.ok(pendingHost.innerHTML.includes('id="tasks-approvals-sort"'));
assert.ok(pendingHost.innerHTML.includes('data-approval-expiring="true"'));
assert.ok(pendingHost.innerHTML.includes('Expiring soon'));
assert.ok(
  pendingHost.innerHTML.indexOf('data-approval-id="urgent-1"') < pendingHost.innerHTML.indexOf('data-approval-id="later-1"'),
  'Expected urgent-1 to render before later-1 when sorted by expiring soonest'
);

// 2. Recent Decisions risk-context filter (#tasks-approval-history-risk)
vm.runInContext(`
  _approvalDecisionHistory = [
    { approval_id: 'dec-safe', tool_name: 'write_file', decision: 'approve_task', digest: 'aa11', external_untrusted_context_seen: false },
    { approval_id: 'dec-ext', tool_name: 'send_email', decision: 'deny', digest: 'bb22', external_untrusted_context_seen: true },
  ];
  _approvalHistoryRiskFilter = 'external';
  _renderApprovalHistory();
`, sandbox);

assert.ok(historyHost.innerHTML.includes('id="tasks-approval-history-risk"'));
assert.ok(historyHost.innerHTML.includes('Recent Decisions (1)'));
assert.ok(historyHost.innerHTML.includes('data-approval-id="dec-ext"'));
assert.ok(!historyHost.innerHTML.includes('data-approval-id="dec-safe"'));
assert.ok(historyHost.innerHTML.includes('id="tasks-approval-history-reset"'));

console.log('PASS: ops-release-v12 TTL urgency sort/highlight & audit risk-context filter UI tests');