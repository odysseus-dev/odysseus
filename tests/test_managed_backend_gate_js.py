"""Managed-launch compatibility gate and exact model ownership; Node executes the real modules, no model runs."""
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
GATE = ROOT / "static" / "js" / "managedBackendGate.js"

NODE = r"""
const gate = require(process.argv[1]);
const input = JSON.parse(require('node:fs').readFileSync(0, 'utf8'));
(async () => {
  let out;
  if (input.op === 'check') {
    const fetchJson = async () => { if (input.throws) throw new Error('refused'); return input.response === null ? null :
      {ok: input.ok !== false, status: input.status || 200, json: async () => { if (input.badJson) throw new Error('bad'); return input.response; }}; };
    out = await gate.checkManagedBackend(fetchJson);
  } else out = gate.verifyManagedSelectable(input.args);
  process.stdout.write(JSON.stringify(out));
})();
"""


def run(payload):
    result = subprocess.run(["node", "-e", NODE, str(GATE)], input=json.dumps(payload), text=True, capture_output=True, timeout=15)
    assert result.returncode == 0, result.stderr
    return json.loads(result.stdout)


def backend_status(**override):
    from src.model_serving_status import MANAGED_SERVING_CONTRACT
    body = {"schema_version": 1, "contract": dict(MANAGED_SERVING_CONTRACT), "services": []}
    body.update(override)
    return body


def check(**kw):
    return run({"op": "check", **kw})


def test_frontend_requirements_equal_the_backend_contract():
    from src.model_serving_status import MANAGED_SERVING_CONTRACT
    assert check(response=backend_status())["ok"] is True
    # exactly the contract the backend advertises; an extra backend key is harmless, a changed value is not
    assert check(response=backend_status(contract={**MANAGED_SERVING_CONTRACT, "future": 1}))["ok"] is True
    assert check(response=backend_status(contract={**MANAGED_SERVING_CONTRACT, "typed_readiness": 2}))["reason"] == "incompatible"


def test_old_backend_without_the_contract_fails_closed():
    old = {"schema_version": 1, "services": []}      # exactly what the 5cf9613f backend returns
    result = check(response=old)
    assert result["ok"] is False and result["reason"] == "missing"


def test_missing_malformed_and_unavailable_backends_fail_closed():
    assert check(response=backend_status(contract="yes"))["reason"] == "malformed"
    assert check(response=backend_status(services=None))["reason"] == "malformed"
    assert check(response=backend_status(schema_version=2))["reason"] == "incompatible"
    assert check(response=["not", "an", "object"])["reason"] == "malformed"
    assert check(response={}, badJson=True)["reason"] == "malformed"
    for kw in ({"ok": False, "status": 404, "response": {}}, {"ok": False, "status": 403, "response": {}},
               {"ok": False, "status": 500, "response": {}}, {"throws": True}, {"response": None}):
        result = check(**kw)
        assert result["ok"] is False and result["reason"] == "unavailable", kw


SID, EID = "sess1", "local-managed-sess1"


def fixtures(**mut):
    row = {"id": SID, "management": "odysseus-managed", "process_ownership": "owned", "endpoint_ownership_verified": True,
           "endpoint_id": EID, "readiness": "ready", "models": ["runtime-id"]}
    args = {"sessionId": SID, "endpointId": EID,
            "ready": {"ok": True, "state": "ready", "models": ["runtime-id"]},
            "status": {"schema_version": 1, "services": [row]},
            "catalogItems": [{"endpoint_id": EID, "models": ["runtime-id"], "offline": False}]}
    for key, fn in mut.items():
        fn(args)
    return args


def verify(**mut):
    return run({"op": "verify", "args": fixtures(**mut)})


def test_exact_owned_endpoint_and_runtime_model_id_is_selectable():
    result = verify()
    assert result == {"ok": True, "reason": "ok", "modelIds": ["runtime-id"]}


def test_user_configured_endpoint_is_never_accepted_even_with_matching_data():
    result = verify(a=lambda a: a.update(endpointId="user-configured"),
                    b=lambda a: a["status"]["services"][0].update(endpoint_id="user-configured"),
                    c=lambda a: a["catalogItems"][0].update(endpoint_id="user-configured"))
    assert result["ok"] is False and "user-configured endpoint" in result["reason"]


def test_wait_ready_alone_is_not_proof():
    assert verify(a=lambda a: a.update(status=None))["ok"] is False
    assert verify(a=lambda a: a["status"]["services"][0].update(endpoint_ownership_verified=False))["ok"] is False
    assert verify(a=lambda a: a["status"]["services"][0].update(process_ownership="foreign"))["ok"] is False
    assert verify(a=lambda a: a["status"]["services"][0].update(management="external"))["ok"] is False
    assert verify(a=lambda a: a["status"]["services"][0].update(readiness="loading"))["ok"] is False
    assert verify(a=lambda a: a["ready"].update(ok=False, state="startup-timeout"))["ok"] is False


def test_endpoint_id_and_model_ids_must_agree_everywhere():
    assert verify(a=lambda a: a["status"]["services"][0].update(endpoint_id="local-managed-other"))["ok"] is False
    assert verify(a=lambda a: a["status"]["services"][0].update(models=[]))["ok"] is False
    assert verify(a=lambda a: a["ready"].update(models=["different-id"]))["ok"] is False
    assert verify(a=lambda a: a["catalogItems"][0].update(models=["Friendly display label"]))["ok"] is False
    assert verify(a=lambda a: a["catalogItems"].clear())["ok"] is False
    assert verify(a=lambda a: a["catalogItems"].append(dict(a["catalogItems"][0])))["ok"] is False   # ambiguous
    assert verify(a=lambda a: a["catalogItems"][0].update(offline=True))["ok"] is False
    assert verify(a=lambda a: a["status"]["services"].append(dict(a["status"]["services"][0])))["ok"] is False


def test_catalog_extra_models_list_counts_and_ordinary_chat_is_not_touched():
    assert verify(a=lambda a: a["catalogItems"][0].update(models=[], models_extra=["runtime-id"]))["ok"] is True
    source = (ROOT / "static" / "js" / "cookbookServe.js").read_text(encoding="utf-8")
    launch = source[source.index("async function _launchManagedLlama("):]
    launch = launch[:launch.index("\n}\n")]
    # fail-closed gate precedes runtime install and every serving request; its failure returns handled (no legacy fallback)
    gate = launch.index("_requireManagedBackend()")
    assert gate < launch.index("_ensureManagedRuntime(") < launch.index("/api/cookbook/serving/plan") < launch.index("/api/cookbook/serving/start")
    assert "return true;   // fail closed" in launch
    assert "Backend activation required" in source
