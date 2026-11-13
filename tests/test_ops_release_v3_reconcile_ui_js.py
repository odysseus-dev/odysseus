from pathlib import Path
import subprocess


ROOT = Path(__file__).resolve().parent.parent


def test_admin_js_effect_reconcile_and_unsettled_filter_in_node():
    admin_js = (ROOT / "static/js/admin.js").read_text(encoding="utf-8")
    start = admin_js.index("let _opsUnsettledOnly = false;")
    end = admin_js.index("async function loadLogs(isAutoPoll = false) {")
    snippet = admin_js[start:end]

    script = f"""
const assert = require('node:assert/strict');

const handlers = {{}};
const reconcileButtons = [
  {{ dataset: {{ reconcileAction: 'cancelled' }}, addEventListener(ev, cb) {{ handlers['reconcile_' + ev] = cb; }} }},
];
const inspectButtons = [
  {{ dataset: {{ inspectRunId: '22222222222222222222222222222222' }}, addEventListener(ev, cb) {{ handlers['inspect_' + ev] = cb; }} }},
];

const noteEl = {{ value: 'operator verified aborted' }};
const statusEl = {{ style: {{}}, textContent: '' }};
const drawerEl = {{
  style: {{ display: 'none' }},
  innerHTML: '',
  querySelectorAll(sel) {{
    if (sel === 'button[data-reconcile-action]') return reconcileButtons;
    return [];
  }},
}};
const unsettledToggleEl = {{
  checked: false,
  addEventListener(ev, cb) {{ handlers['unsettled_' + ev] = cb; }},
}};
const panelEl = {{
  style: {{ display: 'none' }},
  innerHTML: '',
  querySelectorAll(sel) {{
    if (sel === 'button[data-inspect-run-id]') return inspectButtons;
    return [];
  }},
}};

const dom = {{
  'admin-ops-runtime-panel': panelEl,
  'admin-ops-effect-drawer': drawerEl,
  'admin-ops-unsettled-only': unsettledToggleEl,
  'admin-ops-reconcile-note': noteEl,
  'admin-ops-reconcile-status': statusEl,
}};

function el(id) {{ return dom[id] || null; }}

let unsettledState = true;
const fetchCalls = [];
global.fetch = async (url, opts = {{}}) => {{
  fetchCalls.push({{ url, opts }});
  if (url === '/api/diagnostics/ops-summary') {{
    return {{
      ok: true,
      json: async () => ({{
        overall: 'healthy',
        services: {{}},
        ops: {{ tasks: {{ active: 1, paused: 0 }}, approvals: {{ pending_total: 0 }}, effects: {{ recent_runs: 1, external_claim_runs: 0 }} }},
      }}),
    }};
  }}
  if (url.startsWith('/api/diagnostics/effects?')) {{
    const runs = unsettledState
      ? [{{ run_id: '22222222222222222222222222222222', claims: 1, outcomes: 0, unsettled_claims: 1, corrupt: false }}]
      : [];
    return {{ ok: true, json: async () => ({{ runs }}) }};
  }}
  if (url === '/api/diagnostics/effects/22222222222222222222222222222222' && (!opts.method || opts.method === 'GET')) {{
    return {{
      ok: true,
      json: async () => ({{
        run_id: '22222222222222222222222222222222',
        counts: {{ claims: 1, outcomes: unsettledState ? 0 : 1, observations: 0 }},
        reconciliation: {{
          interrupted_claims: [],
          unsettled_claims: unsettledState ? ['eff-orphan-1'] : [],
          needs_attention: unsettledState,
        }},
        records: [
          {{ kind: 'claim', sequence: 1, effect_id: 'eff-orphan-1', tool: 'write_file', external: false }},
        ],
        corrupt: false,
      }}),
    }};
  }}
  if (url === '/api/diagnostics/effects/22222222222222222222222222222222/reconcile' && opts.method === 'POST') {{
    const body = JSON.parse(opts.body);
    assert.equal(body.action, 'cancelled');
    assert.equal(body.note, 'operator verified aborted');
    unsettledState = false;
    return {{
      ok: true,
      json: async () => ({{ reconciled_count: 1, reconciled_effect_ids: ['eff-orphan-1'] }}),
    }};
  }}
  throw new Error('Unexpected fetch: ' + url);
}};

{snippet}

(async () => {{
  await loadAdminOpsPanel();
  assert.equal(panelEl.style.display, 'flex');
  assert.ok(panelEl.innerHTML.includes('1 unsettled'));
  assert.equal(typeof handlers.unsettled_change, 'function');

  // Toggle unsettled_only filter -> requests ?limit=8&unsettled_only=true
  await handlers.unsettled_change({{ target: {{ checked: true }} }});
  assert.ok(fetchCalls.some(c => c.url === '/api/diagnostics/effects?limit=8&unsettled_only=true'));

  // Inspect run -> renders reconcile action bar
  await inspectEffectRun('22222222222222222222222222222222');
  assert.equal(drawerEl.style.display, 'block');
  assert.ok(drawerEl.innerHTML.includes('Mark Cancelled'));
  assert.equal(typeof handlers.reconcile_click, 'function');

  // Click reconcile -> POSTs reconcile and refreshes panel + drawer with 0 unsettled claims
  handlers.reconcile_click();
  await new Promise(r => setImmediate(r));
  assert.ok(fetchCalls.some(c => c.url.endsWith('/reconcile') && c.opts.method === 'POST'));
  assert.ok(panelEl.innerHTML.includes('No unsettled effect runs.'));
  assert.ok(!drawerEl.innerHTML.includes('Mark Cancelled'));
}})().catch(err => {{
  console.error(err);
  process.exit(1);
}});
"""
    res = subprocess.run(["node"], input=script, capture_output=True, text=True, encoding="utf-8", check=False)
    assert res.returncode == 0, res.stderr or res.stdout
