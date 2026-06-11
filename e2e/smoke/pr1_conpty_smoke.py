"""PR-1 ConPTY terminal — Windows smoke test (minimal pass/fail).

Spawns a real pseudo-terminal via ``services.pty.PtySession`` (ConPTY/pywinpty
on Windows, the stdlib ``pty`` module on POSIX), echoes a unique marker through
the shell, and asserts it round-trips back through the PTY. This proves the
core ConPTY plumbing without the full FastAPI/WebSocket stack — that end-to-end
path is the e2e proof ``e2e/test_pty_ws.py`` (PR-4).

``PtySession.read()`` blocks on the underlying handle, so reads run on a daemon
thread polled against a wall-clock deadline — the smoke can never hang.

Run:   python e2e/smoke/pr1_conpty_smoke.py
Exit:  0 = PASS, 1 = FAIL
"""
import os
import sys
import threading
import time

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, ROOT)

MARKER = "ODYSSEUS_PTY_OK_4217"


def main() -> int:
    from services.pty import PtySession, PTY_BACKEND, pty_supported

    ok, err = pty_supported()
    if not ok:
        print(f"FAIL: PTY backend unavailable: {err}")
        return 1
    print(f"PTY backend: {PTY_BACKEND}")

    session = PtySession(cols=80, rows=24)
    session.spawn()

    chunks: list[str] = []
    stop = threading.Event()

    def _reader() -> None:
        while not stop.is_set():
            try:
                data = session.read(4096)
            except Exception:
                break
            if data:
                chunks.append(data)
            else:
                time.sleep(0.05)

    reader = threading.Thread(target=_reader, daemon=True)
    reader.start()

    time.sleep(0.5)  # let the shell banner/prompt come up
    session.write(f"echo {MARKER}\r\n")

    deadline = time.time() + 15
    found = False
    while time.time() < deadline:
        if MARKER in "".join(chunks):
            found = True
            break
        time.sleep(0.1)

    stop.set()
    try:
        session.kill()
    except Exception:
        pass

    if found:
        print(f"PASS: marker round-tripped through the {PTY_BACKEND} PTY")
        return 0
    print("FAIL: marker not observed in PTY output within 15s")
    print("---- last 500 chars captured ----")
    print("".join(chunks)[-500:])
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
