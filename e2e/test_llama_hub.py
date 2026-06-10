"""E2E test for the local llama.cpp hub (/api/llama/*).

Proves the full real loop THROUGH Odysseus's HTTP API (admin-authed):
  status -> (binary already provisioned) -> serve a downloaded GGUF ->
  server becomes ready -> auto-registered as a ModelEndpoint ->
  a REAL completion comes back from the local model.

Assumes the binary is already provisioned and at least one GGUF is downloaded
(the manager-level test does that). Run against a live server with auth ON.
"""
import json
import os
import sys
import time

import requests

PORT = int(os.environ.get("LLAMA_TEST_PORT", "7271"))
BASE = f"http://127.0.0.1:{PORT}"
ADMIN_PW = "PtyTest_Known_123"


def set_admin_password():
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here + "/..")
    import bcrypt
    auth_path = "data/auth.json"
    data = json.load(open(auth_path, encoding="utf-8"))
    data["users"]["admin"]["password_hash"] = bcrypt.hashpw(ADMIN_PW.encode(), bcrypt.gensalt()).decode()
    data["users"]["admin"]["is_admin"] = True
    json.dump(data, open(auth_path, "w", encoding="utf-8"), indent=2)


def main():
    s = requests.Session()
    r = s.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": ADMIN_PW}, timeout=10)
    r.raise_for_status()
    assert s.cookies.get("odysseus_session"), "no session cookie"
    print("[setup] logged in as admin")

    # 1. STATUS
    st = s.get(f"{BASE}/api/llama/status", timeout=10).json()
    print(f"[status] backend={st['backend']} provisioned={st['binary_provisioned']}")
    assert st["binary_provisioned"], "binary not provisioned (run manager test first)"

    # 2. LOCAL MODELS
    lm = s.get(f"{BASE}/api/llama/models/local", timeout=10).json()
    models = lm.get("models", [])
    assert models, "no local GGUF downloaded"
    gguf = models[0]["path"]
    print(f"[models] using {models[0]['name']} ({models[0]['size_mb']} MB)")

    # 3. SERVE (auto-register=True)
    print("[serve] launching…")
    sv = s.post(f"{BASE}/api/llama/serve",
                json={"gguf_path": gguf, "model_id": "e2e-llama", "register": True},
                timeout=180).json()
    assert sv.get("ok"), f"serve failed: {sv}"
    server = sv["server"]
    port = server["port"]
    print(f"[serve] ready on :{port}, endpoint_id={server.get('endpoint_id')}, ngl={server['n_gpu_layers']}")
    assert server.get("endpoint_id"), "not auto-registered as ModelEndpoint"

    # 4. REAL COMPLETION via the local server's OpenAI endpoint
    comp = requests.post(
        f"http://127.0.0.1:{port}/v1/chat/completions",
        json={"model": "e2e-llama",
              "messages": [{"role": "user", "content": "Reply with exactly: HUB_E2E_OK"}],
              "max_tokens": 20, "temperature": 0},
        timeout=60,
    ).json()
    txt = comp["choices"][0]["message"]["content"]
    print(f"[completion] {txt!r}")
    ok = "HUB_E2E_OK" in txt or "HUB" in txt.upper()

    # 5. confirm it shows in the model picker (ModelEndpoint registered)
    eps = s.get(f"{BASE}/api/model-endpoints", timeout=10)
    registered = "e2e-llama" in eps.text or f":{port}" in eps.text
    print(f"[picker] endpoint visible in /api/model-endpoints: {registered}")

    # 6. STOP
    s.post(f"{BASE}/api/llama/stop", json={"model_id": "e2e-llama"}, timeout=10)
    print("[stop] done")

    print("\n=== RESULT ===")
    print(f"serve+ready:        PASS")
    print(f"auto-registered:    {'PASS' if server.get('endpoint_id') else 'FAIL'}")
    print(f"real completion:    {'PASS' if ok else 'FAIL'}")
    print(f"in model picker:    {'PASS' if registered else 'FAIL'}")
    sys.exit(0 if (ok and server.get("endpoint_id")) else 1)


if __name__ == "__main__":
    main()
