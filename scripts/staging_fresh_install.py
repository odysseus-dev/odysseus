"""Fresh-install acceptance flow against an isolated staging instance (never 7000/8100).

usage: staging_fresh_install.py <base-url> <model.gguf> [serve-port]
Drives: GPU detect -> setup plan -> one-click verified install (real release downloads)
-> CUDA verify -> managed start -> native chat endpoint + chat -> stop.
Throwaway admin credentials are random and never printed.
"""
import http.cookiejar
import json
import secrets
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

BASE, MODEL = sys.argv[1].rstrip("/"), sys.argv[2]
PORT = int(sys.argv[3]) if len(sys.argv) > 3 else 18103
assert "7000" not in BASE and PORT != 8100
jar = http.cookiejar.CookieJar()
op = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))


def call(method, path, body=None, form=None, timeout=120):
    data, ctype = None, "application/json"
    if form is not None:
        data, ctype = urllib.parse.urlencode(form).encode(), "application/x-www-form-urlencoded"
    elif body is not None:
        data = json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, data, {"Content-Type": ctype}, method=method)
    try:
        with op.open(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, json.loads(raw) if raw[:1] in b"{[" else raw[:200]
    except urllib.error.HTTPError as exc:
        raw = exc.read()
        try:
            return exc.code, json.loads(raw)
        except ValueError:
            return exc.code, raw[:200]


def show(label, value):
    print(label, json.dumps(value, default=str)[:900], flush=True)


pw = secrets.token_urlsafe(18)
call("POST", "/api/auth/setup", {"username": "fresh-admin", "password": pw})
assert call("POST", "/api/auth/login", {"username": "fresh-admin", "password": pw})[0] == 200

code, gpu = call("GET", "/api/cookbook/serving/gpu")
show("1 gpu-before", [code, {k: gpu.get(k) for k in ("devices", "selected_runtime", "runtimes", "cuda_ready", "detail") if k in gpu}])
code, plan = call("GET", "/api/cookbook/llama-runtime/setup-plan?variant=auto", timeout=60)
show("2 plan", [code, {"tag": plan.get("tag"), "chosen": plan.get("chosen"), "reason": plan.get("reason"), "gpu": plan.get("gpu"),
                       "cuda": {k: plan["cuda"][k] for k in ("total_bytes", "download_bytes", "verifiable", "already_installed")} if plan.get("cuda") else None,
                       "archives": [(a["name"], a["size"], bool(a["sha256"])) for a in plan["cuda"]["archives"]] if plan.get("cuda") else None}])

req = urllib.request.Request(BASE + "/api/cookbook/llama-runtime/setup", json.dumps({"variant": "auto"}).encode(),
                             {"Content-Type": "application/json"}, method="POST")
t0, last, final = time.time(), -1, None
try:
    with op.open(req, timeout=1800) as resp:
        for line in resp:
            if not line.startswith(b"data:"):
                continue
            ev = json.loads(line[5:])
            if ev.get("stream") == "progress" and ev.get("total"):
                pct = int(ev["received"] * 100 / ev["total"]) // 20 * 20
                if pct != last:
                    last = pct
                    print(f"   progress {ev.get('part')}/{ev.get('parts')} {ev['received']}/{ev['total']} t={time.time()-t0:.0f}s", flush=True)
            elif ev.get("stream") in ("done", "error"):
                final = ev
            elif ev.get("detail"):
                print("   ", ev["detail"], flush=True)
except Exception as exc:
    final = {"stream": "exception", "error": repr(exc)}
show("3 install", final)

code, gpu = call("GET", "/api/cookbook/serving/gpu?verify=true", timeout=120)
show("4 gpu-after", [code, {k: gpu.get(k) for k in ("devices", "selected_runtime", "cuda_ready", "verification", "detail") if k in gpu}])
exe = gpu.get("selected_runtime")
if exe and not exe.lower().endswith(".exe"):
    exe = exe.rstrip("\\/") + "\\llama-server.exe"
launch = {"engine": "llamacpp", "executable": exe, "model_path": MODEL, "port": PORT, "context_length": 4096, "gpu_layers": 999,
          "display_name": "Fresh install check"}
code, planned = call("POST", "/api/cookbook/serving/plan", launch)
show("5 serve-plan", [code, {k: planned.get(k) for k in ("gpu_layers", "context_length", "memory_validation", "warnings", "detail")}])
code, started = call("POST", "/api/cookbook/serving/start", launch)
show("6 start", [code, {k: started.get(k) for k in ("session_id", "state", "endpoint_id", "detail")}])
sid, eid = started.get("session_id"), started.get("endpoint_id")
try:
    if sid:
        code, ready = call("POST", "/api/cookbook/serving/wait-ready", {"session_id": sid, "timeout_seconds": 180}, timeout=210)
        show("7 ready", [code, ready.get("state")])
        code, models = call("GET", "/api/models")
        flat = json.dumps(models)
        show("8 catalog-has-endpoint", [code, bool(eid and eid in flat)])
        code, sess = call("POST", "/api/session", form={"name": "fresh-check", "endpoint_id": eid or "", "model": ""}, timeout=60)
        show("9 session", [code, sess if code != 200 else {k: sess.get(k) for k in ("id", "model", "endpoint_url")}])
        if code == 200:
            t1 = time.time()
            code, reply = call("POST", "/api/chat", {"message": "Say hello in five words.", "session": sess["id"],
                                                     "selected_endpoint_id": eid}, timeout=180)
            show("10 native-chat", [code, round(time.time() - t1, 1), str(reply)[:300]])
finally:
    if sid:
        code, stopped = call("POST", "/api/cookbook/serving/stop", {"session_id": sid})
        show("11 stop", [code, stopped.get("state")])
