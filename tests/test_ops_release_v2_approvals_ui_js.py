from pathlib import Path
import re
import subprocess


ROOT = Path(__file__).resolve().parent.parent


def test_tasks_js_live_approval_polling_and_countdown_in_node():
    tasks_js = (ROOT / "static/js/tasks.js").read_text(encoding="utf-8")
    # Extract the approval queue helpers from static/js/tasks.js and run in Node
    start = tasks_js.index("let _pendingApprovals = [];")
    end = tasks_js.index("function _renderList()")
    snippet = tasks_js[start:end]

    script = f"""
const assert = require('node:assert/strict');

let _nowMs = 1_700_000_000_000;
Date.now = () => _nowMs;

const intervals = [];
global.setInterval = (fn, ms) => {{
  const id = intervals.length + 1;
  intervals.push({{ id, fn, ms, cleared: false }});
  return id;
}};
global.clearInterval = (id) => {{
  const entry = intervals.find(x => x.id === id);
  if (entry) entry.cleared = true;
}};

const ttlEl = {{
  dataset: {{ approvalTtl: String(_nowMs / 1000 + 75) }},
  textContent: '',
}};
let refreshClickHandler = null;
const hostEl = {{
  style: {{ display: 'none' }},
  innerHTML: '',
  querySelector(sel) {{
    if (sel === '#tasks-approvals-refresh') {{
      return {{ addEventListener: (ev, cb) => {{ if (ev === 'click') refreshClickHandler = cb; }} }};
    }}
    return null;
  }},
  querySelectorAll(sel) {{
    if (sel === '[data-approval-ttl]') return [ttlEl];
    return [];
  }},
}};

const listeners = {{}};
global.document = {{
  hidden: false,
  getElementById(id) {{ return id === 'tasks-pending-approvals' ? hostEl : null; }},
  addEventListener(ev, cb) {{ listeners[ev] = cb; }},
}};

const fetchCalls = [];
global.fetch = async (url, opts) => {{
  fetchCalls.push({{ url, opts }});
  return {{
    ok: true,
    json: async () => ({{ approvals: _pendingApprovals }}),
  }};
}};

function _esc(v) {{ return String(v ?? ''); }}
async function loadTasks() {{}}

{snippet}

assert.equal(_formatApprovalTtl(0), '');
assert.equal(_formatApprovalTtl(_nowMs / 1000 - 1), 'Expired');
assert.equal(_formatApprovalTtl(_nowMs / 1000 + 42), '42s left');
assert.equal(_formatApprovalTtl(_nowMs / 1000 + 125), '2m 5s left');

_pendingApprovals = [
  {{
    approval_id: 'apr-1',
    tool_name: 'write_file',
    digest: 'abc123',
    content_preview: 'Write config.yaml',
    expires_at: _nowMs / 1000 + 75,
  }},
];

_renderPendingApprovals();
assert.equal(hostEl.style.display, 'flex');
assert.ok(hostEl.innerHTML.includes('Pending Approvals (1)'));
assert.ok(hostEl.innerHTML.includes('1m 15s left'));
assert.equal(typeof refreshClickHandler, 'function');
assert.equal(intervals.filter(x => !x.cleared).length, 2);

// Advance 30s and fire the 1s tick interval -> countdown updates to 45s left
_nowMs += 30_000;
const tickTimer = intervals.find(x => x.ms === 1000 && !x.cleared);
tickTimer.fn();
assert.equal(ttlEl.textContent, '45s left');

// Advance past expiry and fire tick -> expired approval is pruned & host hidden
_nowMs += 50_000;
tickTimer.fn();
assert.equal(_pendingApprovals.length, 0);
assert.equal(hostEl.style.display, 'none');
"""
    res = subprocess.run(["node"], input=script, capture_output=True, text=True, check=False)
    assert res.returncode == 0, res.stderr or res.stdout


def test_ops_release_v2_tasks_asset_version_bumped():
    app_src = (ROOT / "static/app.js").read_text(encoding="utf-8")
    sw_src = (ROOT / "static/sw.js").read_text(encoding="utf-8")
    app_match = re.search(r"tasks\.js\?v=([^'\"]+)", app_src)
    sw_match = re.search(r"tasks\.js\?v=([^'\"]+)", sw_src)
    assert app_match and sw_match
    assert app_match.group(1) == "20261113opsreleasev2"
    assert sw_match.group(1) == app_match.group(1)