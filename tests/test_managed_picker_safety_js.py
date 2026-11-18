"""Run the real catalog refresh, _getAllModels(), and chat send guard with mocked HTTP and no DOM or inference."""
import json
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static" / "js"

NODE = r"""
const fs = require('node:fs'), vm = require('node:vm');
const gate = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const read = f => fs.readFileSync(f, 'utf8').replace(/\r\n/g, '\n');
const models = read(process.argv[2]), picker = read(process.argv[3]), chat = read(process.argv[4]);
const slice = (s, a, b) => s.slice(s.indexOf(a), s.indexOf(b, s.indexOf(a)));
const modelsSrc = slice(models, 'let API_BASE', 'export function init(')
  + slice(models, 'function _startChat(', 'function _buildModelRow(')
  + slice(models, 'let _managedVerdicts =', '  if (!box) return;\n  try {') + '}\n'
  + slice(models, 'export function getCachedItems(', 'const modelsModule');
const pickerSrc = slice(picker, '  function _getAllModels() {', '  function _hasModelCache()');
const pickSrc = slice(picker, 'async function _pick(m) {', "  document.addEventListener('odysseus:auto-select-model'");
const sendGuard = slice(chat, '    // Fail closed for Cookbook-managed endpoints:', '    // Materialize pending session');
const events = [], fetches = [];
let step, finishStatus;
const context = {
  isManagedEndpointId: gate.isManagedEndpointId, filterManagedCatalog: gate.filterManagedCatalog,
  document: { getElementById: () => null }, console: { error: () => {} },
  _localProbe: {}, sortModelObjects: x => x, _pickerModelKey: m => `${m.endpointId || m.url}::${m.mid}`,
  uiModule: { showToast: () => events.push('toast') }, _releaseSendFlag: () => events.push('released'),
  sessionModule: { getSessions: () => [] },
  CustomEvent: class { constructor(t){this.type=t;} },
  window: { dispatchEvent: e => events.push(e.type), sessionModule: { updateModelPicker: () => events.push('picker') } },
  fetch: async url => { fetches.push(url);
    const catalog = url.startsWith('/api/models?');
    const failure = catalog ? step.catalogFailure : step.statusFailure;
    if (!catalog && step.holdStatus) {
      const status = input.status;
      return new Promise(resolve => { finishStatus = () => resolve({ ok: true, json: async () => status }); });
    }
    if (failure === 'timeout') throw new DOMException('timed out', 'TimeoutError');
    if (!catalog && input.statusThrows) throw new Error('down');
    return { ok: failure !== '503' && (catalog || input.statusOk !== false), status: 503,
      json: async () => {
        if (failure === 'json') throw new SyntaxError('invalid JSON');
        if (failure === 'shape') return { items: {} };
        return catalog ? { items: step.items } : input.status;
      } }; },
  AbortSignal, DOMException,
};
vm.createContext(context);
vm.runInContext(modelsSrc.replace(/^export /gm, '') + '\n' + pickerSrc + '\n' + pickSrc
  + '\nasync function send(endpoint_id) { const selectedRouteForSend = { endpoint_id };\n'
  + sendGuard + '\nreturn "passed guard"; }', context);
context.window.modelsModule = { getCachedItems: context.getCachedItems, getManagedVerdicts: context.getManagedVerdicts,
  checkManagedSelection: context.checkManagedSelection };
(async () => {
  const out = [];
  let olderSend;
  for (step of input.steps) {
    if (step.status !== undefined) input.status = step.status;
    let check, send;
    if (step.holdStatus) {
      olderSend = context.send(step.send);
      await new Promise(resolve => setImmediate(resolve));
      if (!finishStatus) throw new Error('did not reach serving/status');
    } else {
      if (!step.send && !gate.isManagedEndpointId(step.check)) await context.refreshModels(true);
      if (step.check) check = await context.checkManagedSelection(step.check);
      if (step.send) send = await context.send(step.send) ?? 'blocked';
      if (step.finishStatus) {
        finishStatus();
        send = await olderSend ?? 'blocked';
      }
    }
    if (step.staleClick) {
      await context._pick({ endpointId: step.staleClick });
      context._startChat('', '', step.staleClick);
    }
    const picked = vm.runInContext('_getAllModels()', context);
    out.push({ picked: picked.map(m => ({ mid: m.mid, endpointId: m.endpointId })), verdicts: Object.fromEntries(Object.entries(context.getManagedVerdicts()).map(([k, v]) => [k, v.ok])), check, send, events: events.splice(0) });
  }
  process.stdout.write(JSON.stringify({ out, fetches }));
})().catch(e => { console.error(e); process.exit(1); });
"""

SID, EID = "s1", "local-managed-s1"
URL = "http://127.0.0.1:18101/v1"


def run(payload):
    r = subprocess.run(["node", "-e", NODE, str(JS / "managedBackendGate.js"), str(JS / "models.js"), str(JS / "modelPicker.js"), str(JS / "chat.js")],
                       input=json.dumps(payload), text=True, capture_output=True, timeout=20)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


CONTRACT = {"serving_api": 1, "managed_chat_catalog": 1, "typed_readiness": 1}


def row(**kw):
    base = {"id": SID, "management": "odysseus-managed", "process_ownership": "owned", "endpoint_ownership_verified": True,
            "endpoint_id": EID, "readiness": "ready", "models": ["runtime-id"]}
    base.update(kw)
    return base


def status(*rows, contract=CONTRACT):
    body = {"schema_version": 1, "services": list(rows)}
    if contract is not None:
        body["contract"] = contract
    return body


def managed_item(models=("runtime-id",), **kw):
    return {"endpoint_id": EID, "endpoint_name": "Friendly label", "url": URL, "category": "local", "models": list(models), **kw}


# same URL, but a user-configured endpoint with a different id and an external one
USER = {"endpoint_id": "user-configured", "endpoint_name": "My own server", "url": URL, "category": "local", "models": ["user-model"]}
EXTERNAL = {"endpoint_id": "ollama-ext", "endpoint_name": "Ollama", "url": "http://127.0.0.1:11434/v1", "category": "local", "models": ["llama3"]}
CLOUD = {"endpoint_id": "cloud", "endpoint_name": "Cloud", "url": "https://api.example.com/v1", "category": "api", "models": ["big-model"]}
ORDINARY = [USER, EXTERNAL, CLOUD]


def mids(step):
    return sorted((m["endpointId"], m["mid"]) for m in step["picked"])


def test_verified_managed_endpoint_and_runtime_model_id_are_selectable():
    out = run({"steps": [{"items": [managed_item(), *ORDINARY], "status": status(row())}]})["out"][0]
    assert (EID, "runtime-id") in mids(out) and out["verdicts"] == {EID: True}


def test_unverified_foreign_stale_offline_and_lost_ownership_are_not_selectable():
    bad_rows = {
        "listener unverified (Sol's reproduction)": row(endpoint_ownership_verified=False),
        "foreign process": row(process_ownership="foreign"),
        "ownership lost": row(process_ownership="gone"),
        "not managed": row(management="external"),
        "loading": row(readiness="loading"),
        "other endpoint id": row(endpoint_id="local-managed-other"),
        "no runtime id": row(models=[]),
    }
    for name, bad in bad_rows.items():
        out = run({"steps": [{"items": [managed_item(), *ORDINARY], "status": status(bad)}]})["out"][0]
        assert not any(e == EID for e, _ in mids(out)), name
        assert out["verdicts"] == {EID: False}, name
    for name, items in {"stale/offline": [managed_item(offline=True)], "display label instead of runtime id": [managed_item(models=["Friendly label"])],
                        "duplicate catalog entry": [managed_item(), managed_item()]}.items():
        out = run({"steps": [{"items": items, "status": status(row())}]})["out"][0]
        assert not any(e == EID for e, _ in mids(out)), name
    out = run({"steps": [{"items": [managed_item()], "status": status()}]})["out"][0]          # no service at all
    assert out["picked"] == []


def test_status_unavailable_or_old_backend_fails_closed_for_managed_only():
    for payload in ({"status": status(row(), contract=None)}, {"status": None, "statusOk": False}, {"status": status(row()), "statusThrows": True},
                    {"status": status(row(), contract={**CONTRACT, "typed_readiness": 2})}):
        out = run({**payload, "steps": [{"items": [managed_item(), *ORDINARY]}]})["out"][0]
        assert not any(e == EID for e, _ in mids(out)), payload
        assert {e for e, _ in mids(out)} == {"user-configured", "ollama-ext", "cloud"}   # ordinary endpoints keep working


def test_same_url_user_configured_and_external_endpoints_are_unaffected():
    out = run({"steps": [{"items": [managed_item(), *ORDINARY], "status": status(row(endpoint_ownership_verified=False))}]})["out"][0]
    assert mids(out) == sorted([("user-configured", "user-model"), ("ollama-ext", "llama3"), ("cloud", "big-model")])
    # with no managed endpoint registered no status request is made at all
    result = run({"steps": [{"items": ORDINARY, "status": status()}]})
    assert result["fetches"] == ["/api/models?refresh=true"] and len(result["out"][0]["picked"]) == 3


def test_previously_selected_managed_endpoint_becomes_unavailable_when_ownership_is_lost():
    steps = [{"items": [managed_item()], "status": status(row()), "check": EID},
             {"items": [managed_item()], "status": status(row(process_ownership="foreign")), "check": EID},
             {"items": [], "status": status(), "check": EID},                                  # endpoint dropped entirely
             {"items": [managed_item()], "status": status(row()), "check": "user-configured"}]
    out = run({"steps": steps})["out"]
    assert out[0]["check"] == {"ok": True} and out[0]["picked"]
    assert out[1]["check"]["ok"] is False and out[1]["picked"] == [] and "picker" in out[1]["events"]   # picker is told to refresh
    assert out[2]["check"]["ok"] is False and "no longer registered" in out[2]["check"]["reason"]
    assert out[3]["check"] is None                                                                   # non-managed: never blocked


def test_send_path_and_picker_label_wiring():
    chat = (JS / "chat.js").read_text(encoding="utf-8")
    guard = chat.index("checkManagedSelection?.(_epId)")
    assert guard < chat.index("Materialize pending session (deferred from model click)") < chat.index("/api/chat_stream")
    assert "_releaseSendFlag();" in chat[guard:guard + 500] and "return;" in chat[guard:guard + 500]
    picker = (JS / "modelPicker.js").read_text(encoding="utf-8")
    assert "_managedUnverified" in picker and "Unavailable" in picker and "setPendingChat(null)" in picker
    assert "isManagedEndpointId(selectedId)" in picker


@pytest.mark.parametrize(("stage", "failure"), [(stage, failure) for stage in ("catalog", "status")
                                                for failure in ("503", "timeout", "json", "shape")])
def test_failed_refresh_revokes_previous_approval(stage, failure):
    good = {"items": [managed_item(), *ORDINARY], "status": status(row())}
    bad = {**good, f"{stage}Failure": failure}
    result = run({"steps": [good, {**bad, "check": EID}, good, {**bad, "send": EID},
                            {**bad, "send": USER["endpoint_id"]}, {**good, "send": EID}]})
    primed, checked, reprimed, blocked, ordinary, recovered = result["out"]
    assert primed["verdicts"] == reprimed["verdicts"] == {EID: True}
    assert checked["check"]["ok"] is False
    assert checked["verdicts"].get(EID) is not True
    assert blocked["send"] == "blocked" and {"toast", "released"} <= set(blocked["events"])
    assert mids(checked) == mids(blocked) == sorted((i["endpoint_id"], i["models"][0]) for i in ORDINARY)
    assert ordinary["send"] == recovered["send"] == "passed guard"
    assert (EID, "runtime-id") in mids(recovered)
    assert result["fetches"].count("/api/cookbook/serving/status") == (3 if stage == "catalog" else 5)


def test_incomplete_refresh_cannot_reapprove_after_newer_503():
    good = {"items": [managed_item(), *ORDINARY], "status": status(row())}
    primed, incomplete, failed = run({"steps": [good, {**good, "holdStatus": True, "send": EID},
                                               {**good, "catalogFailure": "503", "finishStatus": True}]})["out"]
    assert primed["verdicts"] == {EID: True}
    assert failed["send"] == "blocked"
    for step in (incomplete, failed):
        assert step["verdicts"].get(EID) is not True
        assert mids(step) == sorted((i["endpoint_id"], i["models"][0]) for i in ORDINARY)


def test_stale_rendered_rows_cannot_select_after_503():
    good = {"items": [managed_item(), *ORDINARY], "status": status(row())}
    _, failed = run({"steps": [good, {**good, "catalogFailure": "503", "staleClick": EID}]})["out"]
    assert failed["events"].count("toast") == 2


def test_malformed_managed_model_list_cannot_verify():
    good = {"items": [managed_item(models=["x"]), *ORDINARY], "status": status(row(models=["x"]))}
    bad = {**good, "items": [{**good["items"][0], "models": "x"}, *ORDINARY], "send": EID}
    primed, failed = run({"steps": [good, bad]})["out"]
    assert primed["verdicts"] == {EID: True}
    assert failed["send"] == "blocked"
    assert mids(failed) == sorted((i["endpoint_id"], i["models"][0]) for i in ORDINARY)
