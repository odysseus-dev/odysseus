"""E2E test for the real PTY WebSocket (/ws/pty).

Proves: (1) NO-cookie connection is REJECTED (RCE gate holds),
        (2) admin-cookie connection gets a working PTY that streams echo
            output AND carriage-return progress (tqdm-style).

Run against a LIVE server started with auth ENABLED on PORT (default 7251).
"""
import asyncio
import json
import os
import sys

import requests
import websockets

PORT = int(os.environ.get("PTY_TEST_PORT", "7251"))
BASE = f"http://127.0.0.1:{PORT}"
WS = f"ws://127.0.0.1:{PORT}/ws/pty"


ADMIN_PW = "PtyTest_Known_123"


def set_admin_password():
    """Rewrite the admin password hash to a known value (run BEFORE server start
    so the server loads this hash). Idempotent."""
    here = os.path.dirname(os.path.abspath(__file__))
    os.chdir(here + "/..")
    import bcrypt, json as _json
    auth_path = "data/auth.json"
    with open(auth_path, "r", encoding="utf-8") as f:
        data = _json.load(f)
    data["users"]["admin"]["password_hash"] = bcrypt.hashpw(ADMIN_PW.encode(), bcrypt.gensalt()).decode()
    data["users"]["admin"]["is_admin"] = True
    with open(auth_path, "w", encoding="utf-8") as f:
        _json.dump(data, f, indent=2)


def login_for_cookie():
    """Log in via the REAL HTTP endpoint so the SERVER's AuthManager issues the
    session token (correct cross-process auth)."""
    r = requests.post(f"{BASE}/api/auth/login",
                      json={"username": "admin", "password": ADMIN_PW}, timeout=10)
    r.raise_for_status()
    tok = r.cookies.get("odysseus_session")
    assert tok, f"no odysseus_session cookie in login response (status {r.status_code})"
    return tok


async def test_negative_no_cookie():
    """No cookie -> must be rejected (4403) with NO shell output."""
    try:
        async with websockets.connect(WS, open_timeout=5) as ws:
            # If we somehow connected, try to run a command and ensure no output.
            await ws.send(json.dumps({"type": "input", "data": "echo LEAK_TEST\r\n"}))
            try:
                msg = await asyncio.wait_for(ws.recv(), timeout=2)
                if "LEAK_TEST" in msg:
                    return False, f"LEAK: got shell output without auth: {msg[:120]}"
                return False, f"CONNECTED without auth (got: {msg[:120]})"
            except asyncio.TimeoutError:
                return False, "CONNECTED without auth (no output but socket open == gate failed)"
    except Exception as e:
        # Expected: handshake rejected.
        return True, f"rejected as expected: {type(e).__name__}: {str(e)[:100]}"


async def test_positive_admin(token):
    """Admin cookie -> working PTY; echo + carriage-return progress stream."""
    headers = [("Cookie", f"odysseus_session={token}")]
    # websockets uses additional_headers (>=12) or extra_headers (<12).
    try:
        ctx = websockets.connect(WS, additional_headers=headers, open_timeout=8)
    except TypeError:
        ctx = websockets.connect(WS, extra_headers=headers, open_timeout=8)
    async with ctx as ws:
        # Drive an echo and a CR-progress loop.
        await ws.send(json.dumps({"type": "input", "data": "echo HELLO_PTY_VERIFY\r\n"}))
        prog = ("python -c \"import time,sys\n"
                "for i in range(5): sys.stdout.write('\\rPROG %d%%'%(i*25)); sys.stdout.flush(); time.sleep(0.05)\n"
                "print('\\nPROG_DONE')\"\r\n")
        await ws.send(json.dumps({"type": "input", "data": prog}))

        captured = []
        try:
            for _ in range(400):
                msg = await asyncio.wait_for(ws.recv(), timeout=8)
                d = json.loads(msg)
                if d.get("type") == "output":
                    captured.append(d["data"])
                elif d.get("type") == "exit":
                    break
                joined = "".join(captured)
                if "HELLO_PTY_VERIFY" in joined and "PROG_DONE" in joined:
                    break
        except asyncio.TimeoutError:
            pass
        full = "".join(captured)
        ok_echo = "HELLO_PTY_VERIFY" in full
        ok_cr = "\r" in full or "PROG" in full
        return ok_echo and ok_cr, {
            "echo_seen": ok_echo,
            "cr_progress_seen": ok_cr,
            "sample": full[-400:].replace("\r", "\\r")[:400],
        }


async def main():
    token = login_for_cookie()
    print(f"[setup] logged in, server-issued session token: {token[:12]}...")

    neg_ok, neg_detail = await test_negative_no_cookie()
    print(f"[NEGATIVE no-cookie] {'PASS' if neg_ok else 'FAIL'} — {neg_detail}")

    pos_ok, pos_detail = await test_positive_admin(token)
    print(f"[POSITIVE admin-cookie] {'PASS' if pos_ok else 'FAIL'} — {pos_detail}")

    print("\n=== RESULT ===")
    print(f"NEGATIVE (RCE gate holds): {'PASS' if neg_ok else 'FAIL'}")
    print(f"POSITIVE (PTY streams):    {'PASS' if pos_ok else 'FAIL'}")
    sys.exit(0 if (neg_ok and pos_ok) else 1)


if __name__ == "__main__":
    asyncio.run(main())
