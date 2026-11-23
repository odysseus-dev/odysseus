"""A detached Notes descendant must not hold output open or kill a bystander."""
import os
import runpy
import subprocess
import sys
from pathlib import Path

import pytest
from src import process_lifecycle as lifecycle

ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(os.name == "nt", reason="POSIX detached-session regression; Windows uses taskkill tree")
def test_notes_cleanup_reaps_detached_pipe_holder_and_preserves_bystander():
    stop = runpy.run_path(str(ROOT / "scripts/verify_notes_browser_runtime.py"))["_stop_owned_server"]
    child = "import time; time.sleep(60)"
    program = ("import subprocess,sys,time; "
               f"p=subprocess.Popen([sys.executable,'-c',{child!r}],start_new_session=True); "
               "print(p.pid,flush=True); time.sleep(60)")
    with subprocess.Popen([sys.executable, "-c", child]) as bystander:
        server = subprocess.Popen([sys.executable, "-c", program], stdout=subprocess.PIPE,
                                  stderr=subprocess.STDOUT, text=True, start_new_session=True)
        root_identity = lifecycle.ProcessIdentity.capture(server.pid)
        detached = None
        try:
            child_pid = int(server.stdout.readline())
            detached = lifecycle.ProcessIdentity.capture(child_pid)
            assert os.getpgid(child_pid) == child_pid
            stop(server, root_identity)
            assert server.poll() is not None
            assert detached.exited()
            assert bystander.poll() is None, "cleanup touched an unrelated process"
        finally:
            # Test-owned fallback cleanup also runs when the negative control
            # intentionally substitutes the old parent-only teardown.
            targets = [identity for identity in (detached, root_identity) if identity]
            lifecycle.terminate_identities(targets, steps=lifecycle.term_kill_steps(1))
            server.wait(timeout=3)
            if server.stdout:
                server.stdout.close()
            bystander.terminate()
            bystander.wait(timeout=3)
