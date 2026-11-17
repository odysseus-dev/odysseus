"""Authenticated staging check of the managed-serving and runtime-setup endpoints.

usage: staging_auth_check.py <base-url> <llama-server.exe> <model.gguf>
Creates throwaway staging-only accounts with random passwords (never printed) in the
staging instance's own database. Never run this against production.
"""
import http.cookiejar
import json
import secrets
import sys
import time
import urllib.error
import urllib.request

BASE, EXE, MODEL = sys.argv[1].rstrip("/"), sys.argv[2], sys.argv[3]
assert "7000" not in BASE and "8100" not in BASE, "staging only"


class Client:
    def __init__(self):
        self.jar = http.cookiejar.CookieJar()
        self.opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def call(self, method, path, body=None, headers=None, timeout=120):
        data = json.dumps(body).encode() if body is not None else None
        req = urllib.request.Request(BASE + path, data, {"Content-Type": "application/json", **(headers or {})}, method=method)
        try:
            with self.opener.open(req, timeout=timeout) as resp:
                raw = resp.read()
                return resp.status, (json.loads(raw) if raw[:1] in b"{[" else raw[:80])
        except urllib.error.HTTPError as exc:
            raw = exc.read()
            try:
                return exc.code, json.loads(raw)
            except ValueError:
                return exc.code, raw[:80]


admin_pw, user_pw = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
anon, admin, plain = Client(), Client(), Client()
out = {}
out["setup"] = anon.call("POST", "/api/auth/setup", {"username": "stage-admin", "password": admin_pw})[0]
out["admin_login"] = admin.call("POST", "/api/auth/login", {"username": "stage-admin", "password": admin_pw})[0]
out["create_plain_user"] = admin.call("POST", "/api/auth/users", {"username": "stage-user", "password": user_pw, "is_admin": False})[0]
out["plain_login"] = plain.call("POST", "/api/auth/login", {"username": "stage-user", "password": user_pw})[0]

launch = {"engine": "llamacpp", "executable": EXE, "model_path": MODEL, "port": 18102,
          "context_length": 2048, "gpu_layers": 999}
probes = [("GET", "/api/cookbook/serving/status", None), ("GET", "/api/cookbook/serving/gpu", None),
          ("POST", "/api/cookbook/serving/plan", launch), ("POST", "/api/cookbook/serving/start", launch),
          ("POST", "/api/cookbook/serving/stop", {"session_id": "serve-00000000"}),
          ("POST", "/api/cookbook/serving/restart", {"session_id": "serve-00000000"}),
          ("GET", "/api/cookbook/llama-runtime/setup-plan", None),
          ("POST", "/api/cookbook/llama-runtime/setup", {"variant": "auto"})]
for who, client in (("anonymous", anon), ("non_admin", plain)):
    out[who] = {f"{m} {p.rsplit('/', 1)[1]}": (lambda r: [r[0], (r[1].get("detail") if isinstance(r[1], dict) else str(r[1]))])(client.call(m, p, b))
                for m, p, b in probes}
out["cross_site_admin"] = {f"{m} {p.rsplit('/', 1)[1]}": admin.call(m, p, b, {"Sec-Fetch-Site": "cross-site"})[0]
                           for m, p, b in probes if m == "POST" and p.rsplit("/", 1)[1] in ("start", "stop", "restart", "setup")}
out["admin"] = {"status": admin.call("GET", "/api/cookbook/serving/status")[0],
                "gpu": admin.call("GET", "/api/cookbook/serving/gpu")[0],
                "plan": admin.call("POST", "/api/cookbook/serving/plan", launch)[0],
                "setup_plan": admin.call("GET", "/api/cookbook/llama-runtime/setup-plan")[0]}
code, started = admin.call("POST", "/api/cookbook/serving/start", launch)
out["admin"]["start"] = [code, started.get("state") if isinstance(started, dict) else started]
sid = started.get("session_id") if isinstance(started, dict) else None
if sid:
    out["plain_cannot_stop_admins_server"] = plain.call("POST", "/api/cookbook/serving/stop", {"session_id": sid})[0]
    code, ready = admin.call("POST", "/api/cookbook/serving/wait-ready", {"session_id": sid, "timeout_seconds": 120}, timeout=150)
    out["admin"]["wait_ready"] = [code, ready.get("state")]
    code, stopped = admin.call("POST", "/api/cookbook/serving/stop", {"session_id": sid})
    out["admin"]["stop"] = [code, stopped.get("state")]
print(json.dumps(out, indent=1))
# Browser-facing cookie of the non-admin account, for the UI error check (staging only).
if len(sys.argv) > 4 and sys.argv[4] == "--cookie":
    print("COOKIE", {c.name: c.value for c in plain.jar})
