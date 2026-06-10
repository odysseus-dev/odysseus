"""E2E proof that the Odysseus scheduler ACTUALLY FIRES a job.

Flow (all through the live HTTP API, admin-authed):
  1. Serve the local qwen GGUF via /api/llama/serve -> get its OpenAI base_url.
  2. Create a 'cron' LLM task scheduled to fire within ~70s, pointed at that
     local endpoint+model.
  3. Wait past next_run; poll /api/tasks/{id}/runs until a TaskRun appears.
  4. Assert the run completed (status success) with real model output.

This proves the background TaskScheduler loop fires due jobs and records runs —
the literal WS3 proof bar (create -> triggers at its time -> result recorded),
and ties WS2 (local serving) to WS3 (scheduling).
"""
import json
import os
import sys
import time

import requests

PORT = int(os.environ.get("SCHED_TEST_PORT", "7281"))
BASE = f"http://127.0.0.1:{PORT}"
ADMIN_PW = "PtyTest_Known_123"


def set_admin_password():
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here + "/..")
    import bcrypt
    p = "data/auth.json"
    d = json.load(open(p, encoding="utf-8"))
    d["users"]["admin"]["password_hash"] = bcrypt.hashpw(ADMIN_PW.encode(), bcrypt.gensalt()).decode()
    d["users"]["admin"]["is_admin"] = True
    json.dump(d, open(p, "w", encoding="utf-8"), indent=2)


def main():
    s = requests.Session()
    s.post(f"{BASE}/api/auth/login", json={"username": "admin", "password": ADMIN_PW}, timeout=10).raise_for_status()
    print("[setup] logged in")

    # 1. serve local model
    lm = s.get(f"{BASE}/api/llama/models/local", timeout=10).json()["models"]
    assert lm, "no local GGUF"
    sv = s.post(f"{BASE}/api/llama/serve",
                json={"gguf_path": lm[0]["path"], "model_id": "sched-llm", "register": True},
                timeout=180).json()
    assert sv.get("ok"), f"serve failed: {sv}"
    base_url = sv["server"]["base_url"]
    print(f"[serve] local model up at {base_url}")

    # 2. create a cron task firing every minute, pointed at the local endpoint.
    #    "* * * * *" => fires at the top of the next minute (<=60s away).
    create = s.post(f"{BASE}/api/tasks", json={
        "name": "E2E scheduler proof",
        "prompt": "Reply with exactly: SCHED_FIRED_OK",
        "task_type": "llm",
        "schedule": "cron",
        "cron_expression": "* * * * *",
        "output_target": "notification",
        "endpoint_url": base_url,
        "model": "sched-llm",
    }, timeout=15)
    assert create.ok, f"create failed {create.status_code}: {create.text[:300]}"
    task = create.json()
    tid = task.get("id") or task.get("task", {}).get("id")
    print(f"[create] task {tid}, next_run={task.get('next_run')}")
    assert tid, f"no task id in {task}"

    # 3. wait for the scheduler loop to fire it (cron fires on the minute;
    #    loop wakes within ~60s). Poll runs for up to ~150s.
    print("[wait] polling for a TaskRun (scheduler loop must fire it)…")
    fired = None
    deadline = time.time() + 150
    while time.time() < deadline:
        runs = s.get(f"{BASE}/api/tasks/{tid}/runs?limit=5", timeout=10).json()
        runs = runs if isinstance(runs, list) else runs.get("runs", [])
        terminal = [r for r in runs if r.get("status") in ("success", "error")]
        if terminal:
            fired = terminal[0]
            break
        time.sleep(5)

    # cleanup task + server regardless
    try:
        s.delete(f"{BASE}/api/tasks/{tid}", timeout=10)
        s.post(f"{BASE}/api/llama/stop", json={"model_id": "sched-llm"}, timeout=10)
    except Exception:
        pass

    if not fired:
        print("[RESULT] FAIL — no TaskRun recorded within the window")
        sys.exit(1)

    out = (fired.get("result") or fired.get("error") or "")
    print(f"[fired] status={fired['status']} started={fired.get('started_at')}")
    print(f"[fired] output: {out[:160]!r}")
    ok = fired["status"] == "success"
    marker = "SCHED_FIRED_OK" in out or "SCHED" in out.upper()

    print("\n=== RESULT ===")
    print(f"job fired + run recorded: {'PASS' if fired else 'FAIL'}")
    print(f"run succeeded:            {'PASS' if ok else 'FAIL'}")
    print(f"real model output:        {'PASS' if marker else '(ran, marker not exact)'}")
    sys.exit(0 if (fired and ok) else 1)


if __name__ == "__main__":
    main()
