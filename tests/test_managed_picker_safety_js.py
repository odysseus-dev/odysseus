"""Ordinary model-picker safety for Cookbook-managed endpoints. Runs the real _getAllModels() and the real models.js verdict code
(extracted from source, DOM-free); the shared verifier is the real managedBackendGate.js. No model runs."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "static" / "js"

NODE = r"""
const fs = require('node:fs'), vm = require('node:vm');
const gate = require(process.argv[1]);
const input = JSON.parse(fs.readFileSync(0, 'utf8'));
const read = f => fs.readFileSync(f, 'utf8').replace(/\r\n/g, '\n');
const models = read(process.argv[2]), picker = read(process.argv[3]);
const slice = (s, a, b) => s.slice(s.indexOf(a), s.indexOf(b, s.indexOf(a)));
const modelsSrc = slice(models, 'let _rawItems = [];', 'export async function refreshModels(').replace(/^let /gm, 'var ').replace(/export async function/g, 'async function');
const getSrc = 'function getCachedItems() { return _cachedItems; }\nfunction getManagedVerdicts() { return _managedVerdicts; }';
const pickerSrc = slice(picker, '  function _getAllModels() {', '  function _hasModelCache()');
const events = [], fetches = [];
const context = {
  API_BASE: '', isManagedEndpointId: gate.isManagedEndpointId, filterManagedCatalog: gate.filterManagedCatalog, JSON, Object,
  _cachedItems: [], _localProbe: {}, sortModelObjects: x => x, _pickerModelKey: m => `${m.endpointId || m.url}::${m.mid}`,
  CustomEvent: class { constructor(t){this.type=t;} },
  window: { dispatchEvent: e => events.push(e.type), sessionModule: { updateModelPicker: () => events.push('picker') } },
  fetch: async url => { fetches.push(url);
    if (input.statusThrows) throw new Error('down');
    return { ok: input.statusOk !== false, json: async () => input.status }; },
};
context.refreshModels = async () => { await context._applyManagedVerdicts(); };
vm.createContext(context);
vm.runInContext(modelsSrc + '\n' + getSrc + '\n' + pickerSrc, context);
context.window.modelsModule = { getCachedItems: context.getCachedItems };
(async () => {
  const out = [];
  for (const step of input.steps) {
    context._rawItems = step.items; context._cachedItems = step.items;
    if (step.status !== undefined) input.status = step.status;
    await context._applyManagedVerdicts();
    const picked = vm.runInContext('_getAllModels()', context);
    const check = step.check ? await context.checkManagedSelection(step.check) : undefined;
    out.push({ picked: picked.map(m => ({ mid: m.mid, endpointId: m.endpointId })), verdicts: Object.fromEntries(Object.entries(context.getManagedVerdicts()).map(([k, v]) => [k, v.ok])), check, events: events.splice(0) });
  }
  process.stdout.write(JSON.stringify({ out, fetches }));
})().catch(e => { console.error(e); process.exit(1); });
"""

SID, EID = "s1", "local-managed-s1"
URL = "http://127.0.0.1:18101/v1"


def run(payload):
    r = subprocess.run(["node", "-e", NODE, str(JS / "managedBackendGate.js"), str(JS / "models.js"), str(JS / "modelPicker.js")],
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
    assert result["fetches"] == [] and len(result["out"][0]["picked"]) == 3


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
