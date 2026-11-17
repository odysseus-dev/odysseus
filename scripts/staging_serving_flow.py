"""Drive the managed serving API of a staging instance end to end.

usage: staging_serving_flow.py <base-url> <llama-server.exe> <model.gguf> [port]
Never uses port 8100. Stops only the session it started.
"""
import json
import sys
import time
import urllib.error
import urllib.request

BASE, EXE, MODEL = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
PORT = int(sys.argv[4]) if len(sys.argv) > 4 else 18101
assert PORT != 8100


def call(method, path, body=None, timeout=120):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(BASE + path, data, {"Content-Type": "application/json"}, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return resp.status, json.load(resp)
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def service(sid):
    return next((s for s in call("GET", "/api/cookbook/serving/status")[1]["services"] if s["id"] == sid), None)


launch = {"engine": "llamacpp", "executable": EXE, "model_path": MODEL, "port": PORT,
          "context_length": 2048, "gpu_layers": 999}
out = {}
code, plan = call("POST", "/api/cookbook/serving/plan", launch)
out["plan"] = {"http": code, **{k: plan.get(k) for k in ("gpu_layers", "context_length", "memory_validation", "warnings", "detail")}}
code, started = call("POST", "/api/cookbook/serving/start", launch)
out["start"] = {"http": code, **started}
sid = started.get("session_id")
try:
    if sid:
        loading = service(sid)
        out["status_while_loading"] = {k: loading[k] for k in ("readiness", "lifecycle_state", "process_ownership", "management")}
        code, ready = call("POST", "/api/cookbook/serving/wait-ready", {"session_id": sid, "timeout_seconds": 120}, 150)
        out["wait_ready"] = {"http": code, "ok": ready.get("ok"), "state": ready.get("state")}
        running = service(sid)
        out["status_running"] = {k: running[k] for k in ("readiness", "lifecycle_state", "process_ownership", "management",
                                                           "context_length", "base_url", "controls")}
        body = json.dumps({"model": "x", "max_tokens": 48, "messages": [{"role": "user", "content": "Say hello in five words."}]}).encode()
        t0 = time.time()
        with urllib.request.urlopen(urllib.request.Request(running["base_url"] + "/v1/chat/completions", body,
                                                           {"Content-Type": "application/json"}), timeout=120) as resp:
            reply = json.load(resp)
        out["chat"] = {"seconds": round(time.time() - t0, 2), "usage": reply.get("usage"),
                       "text": (reply["choices"][0]["message"].get("content") or reply["choices"][0]["message"].get("reasoning_content") or "")[:100]}
finally:
    if sid:
        code, stopped = call("POST", "/api/cookbook/serving/stop", {"session_id": sid})
        out["stop"] = {"http": code, **{k: stopped.get(k) for k in ("ok", "state")}}
        time.sleep(2)
        after = service(sid)
        out["status_after_stop"] = {k: after[k] for k in ("readiness", "lifecycle_state", "process_ownership")} if after else None
print(json.dumps(out, indent=1))
