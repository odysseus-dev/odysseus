"""Automated regression coverage for document persistence and lifecycle.

Validates autosave document identity, closing active/inactive tabs, session
switching isolation, slow and failed backend responses, queue serialization
with concurrent edits, document deletion cancellation, save-state transitions,
and prevention of cross-document overwrites.
"""

import atexit
import collections
import json
import os
import queue
import signal
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
_IS_WINDOWS = os.name == "nt"

STARTUP_TIMEOUT = 20.0
SCENARIO_TIMEOUT = 30.0


class LifecycleBrowserWorker:
    """Persistent Node and Playwright worker managing isolated browser contexts."""

    def __init__(
        self,
        startup_timeout: float = STARTUP_TIMEOUT,
        scenario_timeout: float = SCENARIO_TIMEOUT,
        cmd: list[str] | None = None,
    ):
        self._proc = None
        self._pgid = None
        self._startup_timeout = startup_timeout
        self._scenario_timeout = scenario_timeout
        self._cmd = cmd
        self._stdout_queue = queue.Queue()
        self._stderr_lines = collections.deque(maxlen=100)
        self._stdout_thread = None
        self._stderr_thread = None

    def _drain_stdout(self, proc: subprocess.Popen):
        try:
            for line in proc.stdout:
                self._stdout_queue.put(line)
        except Exception:
            pass
        finally:
            self._stdout_queue.put(None)

    def _drain_stderr(self, proc: subprocess.Popen):
        try:
            for line in proc.stderr:
                self._stderr_lines.append(line)
        except Exception:
            pass

    def _get_stderr_diagnostics(self) -> str:
        lines = list(self._stderr_lines)
        return "".join(lines).strip() if lines else "(no stderr output recorded)"

    def _terminate_process_group(self, proc=None, pgid=None):
        proc = self._proc if proc is None else proc
        pgid = self._pgid if pgid is None else pgid

        if _IS_WINDOWS:
            if proc is None:
                return
            # taskkill /T terminates Chromium descendants as well as the Node worker.
            try:
                subprocess.run(
                    ["taskkill", "/PID", str(proc.pid), "/T", "/F"],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
            if proc.poll() is None:
                try:
                    proc.kill()
                except OSError:
                    pass
            try:
                proc.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass
            return

        if pgid is None:
            return

        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

        deadline = time.monotonic() + 0.8
        while time.monotonic() < deadline:
            try:
                os.killpg(pgid, 0)
                time.sleep(0.05)
            except (ProcessLookupError, OSError):
                break
        else:
            try:
                os.killpg(pgid, signal.SIGKILL)
            except (ProcessLookupError, OSError):
                pass

        if proc is not None:
            try:
                proc.wait(timeout=0.5)
            except (OSError, subprocess.TimeoutExpired):
                pass

    def start(self):
        if self._proc is not None:
            if self._proc.poll() is None:
                return
            exit_code = self._proc.poll()
            self.close()
            raise RuntimeError(
                f"Persistence browser worker was terminated unexpectedly with code {exit_code}.\n"
                f"STDERR:\n{self._get_stderr_diagnostics()}"
            )

        runner_path = ROOT / "tests" / "helpers" / "document_persistence_lifecycle_runner.mjs"
        env = os.environ.copy()
        self._stdout_queue = queue.Queue()
        self._stderr_lines.clear()

        cmd = self._cmd if self._cmd is not None else ["node", str(runner_path), "--worker"]
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=ROOT,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,
                start_new_session=(not _IS_WINDOWS),
                creationflags=(subprocess.CREATE_NEW_PROCESS_GROUP if _IS_WINDOWS else 0),
                env=env,
            )
            if not _IS_WINDOWS:
                try:
                    self._pgid = os.getpgid(self._proc.pid)
                except OSError:
                    self._pgid = self._proc.pid
        except Exception as exc:
            raise RuntimeError(f"Failed to spawn persistence browser worker process: {exc}") from exc

        self._stdout_thread = threading.Thread(target=self._drain_stdout, args=(self._proc,), daemon=True)
        self._stderr_thread = threading.Thread(target=self._drain_stderr, args=(self._proc,), daemon=True)
        self._stdout_thread.start()
        self._stderr_thread.start()

        try:
            ready_line = self._stdout_queue.get(timeout=self._startup_timeout)
        except queue.Empty:
            self.close()
            raise TimeoutError(
                f"Persistence browser worker failed to start within {self._startup_timeout}s.\n"
                f"STDERR:\n{self._get_stderr_diagnostics()}"
            )

        if ready_line is None:
            try:
                exit_code = self._proc.wait(timeout=1.0)
            except Exception:
                exit_code = self._proc.poll()
            self.close()
            raise RuntimeError(
                f"Persistence browser worker exited unexpectedly with code {exit_code} before ready handshake.\n"
                f"STDERR:\n{self._get_stderr_diagnostics()}"
            )

        try:
            ready_data = json.loads(ready_line)
        except json.JSONDecodeError as exc:
            self.close()
            raise RuntimeError(
                f"Persistence browser worker emitted malformed handshake: {ready_line!r}\n"
                f"STDERR:\n{self._get_stderr_diagnostics()}"
            ) from exc

        if ready_data.get("status") != "ready":
            self.close()
            raise RuntimeError(
                f"Unexpected handshake response from persistence browser worker: {ready_data}\n"
                f"STDERR:\n{self._get_stderr_diagnostics()}"
            )

    def send_command(self, command: dict, timeout: float = SCENARIO_TIMEOUT) -> dict:
        self.start()

        if self._proc.poll() is not None:
            exit_code = self._proc.poll()
            self.close()
            raise RuntimeError(
                f"Cannot deliver command: worker terminated unexpectedly with code {exit_code}.\n"
                f"STDERR:\n{self._get_stderr_diagnostics()}"
            )

        req = json.dumps(command) + "\n"
        try:
            self._proc.stdin.write(req)
            self._proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            exit_code = self._proc.poll()
            self.close()
            raise RuntimeError(
                f"Failed to deliver command to browser worker (exit code: {exit_code}): {exc}\n"
                f"STDERR:\n{self._get_stderr_diagnostics()}"
            ) from exc

        try:
            resp_line = self._stdout_queue.get(timeout=timeout)
        except queue.Empty:
            self._terminate_process_group()
            self.close()
            raise TimeoutError(
                f"Worker timed out after {timeout}s without response.\n"
                f"STDERR:\n{self._get_stderr_diagnostics()}"
            )

        if resp_line is None:
            try:
                exit_code = self._proc.wait(timeout=1.0)
            except Exception:
                exit_code = self._proc.poll()
            self.close()
            raise RuntimeError(
                f"Browser worker closed stdout unexpectedly (exit code: {exit_code}) while waiting for response.\n"
                f"STDERR:\n{self._get_stderr_diagnostics()}"
            )

        try:
            return json.loads(resp_line)
        except json.JSONDecodeError as exc:
            self.close()
            raise RuntimeError(
                f"Worker returned malformed JSON: {resp_line!r}\n"
                f"STDERR:\n{self._get_stderr_diagnostics()}"
            ) from exc

    def run_scenario(self, scenario_name: str) -> dict:
        resp = self.send_command({"scenario": scenario_name}, timeout=self._scenario_timeout)
        if not resp.get("ok"):
            error_msg = resp.get("error", "Unknown worker error")
            raise RuntimeError(
                f"Scenario '{scenario_name}' failed in browser runner:\n{error_msg}\n"
                f"STDERR:\n{self._get_stderr_diagnostics()}"
            )
        return resp["data"]

    def close(self):
        proc = self._proc
        pgid = self._pgid
        self._proc = None
        self._pgid = None

        if proc is None and pgid is None:
            return

        if proc is not None and proc.poll() is None and proc.stdin:
            try:
                proc.stdin.write(json.dumps({"action": "close"}) + "\n")
                proc.stdin.flush()
                proc.wait(timeout=1.5)
            except Exception:
                pass

        descendants_alive = False
        if not _IS_WINDOWS and pgid is not None:
            try:
                os.killpg(pgid, 0)
                descendants_alive = True
            except (ProcessLookupError, OSError):
                pass

        if (proc is not None and proc.poll() is None) or descendants_alive:
            self._terminate_process_group(proc=proc, pgid=pgid)

        if proc is not None:
            try:
                proc.wait(timeout=0.5)
            except Exception:
                pass
            for pipe in (proc.stdin, proc.stdout, proc.stderr):
                if pipe:
                    try:
                        pipe.close()
                    except Exception:
                        pass


_worker = None


def _get_worker() -> LifecycleBrowserWorker:
    global _worker
    if _worker is None:
        _worker = LifecycleBrowserWorker()
        atexit.register(_worker.close)
    return _worker


@pytest.fixture(scope="module")
def _lifecycle_browser_session():
    worker = _get_worker()
    worker.start()
    try:
        yield worker
    finally:
        worker.close()


def _run_lifecycle_scenario(scenario_name: str) -> dict:
    return _get_worker().run_scenario(scenario_name)


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_scenario_1_autosave_identity_on_document_switch():
    """Scenario 1: Edit doc A, switch to B before autosave fires.

    Verifies A is saved with A's content and B is not overwritten.
    """
    data = _run_lifecycle_scenario("scenario_1")
    doc_a_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/doc-a")]
    doc_b_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/doc-b")]
    assert len(doc_a_puts) == 1
    assert doc_a_puts[0]["body"]["content"] == "A modified"
    assert len(doc_b_puts) == 0
    assert data["activeValue"] == "B initial"


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_scenario_2_close_active_document_flushes_pending_autosave():
    """Scenario 2: Edit doc A, close A before autosave.

    Verifies A is saved with A's content and no error toast is raised.
    """
    data = _run_lifecycle_scenario("scenario_2")
    doc_a_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/doc-a")]
    assert len(doc_a_puts) == 1
    assert doc_a_puts[0]["body"]["content"] == "A closing edit"
    assert data["activeValue"] == "B initial"
    assert "Save failed" not in data["toast"]
    assert "could not be saved" not in data["toast"]


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_scenario_3_close_inactive_document_preserves_active_edits():
    """Scenario 3: Close inactive doc B while active doc A has pending edits.

    Verifies B is closed, A's pending autosave/dirty state is untouched.
    """
    data = _run_lifecycle_scenario("scenario_3")
    assert data["activeAfterClose"] == "doc-a"
    assert data["saveStateAfterClose"] == "dirty"
    assert data["saveStateAfterAutoSave"] == "saved"
    doc_a_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/doc-a")]
    assert len(doc_a_puts) == 1
    assert doc_a_puts[0]["body"]["content"] == "A pending edit"


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_scenario_4_switch_session_while_save_is_pending():
    """Scenario 4: Switch sessions while a save is pending.

    Verifies previous session doc save does not target or affect new session.
    """
    data = _run_lifecycle_scenario("scenario_4")
    s1_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/doc-s1")]
    s2_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/doc-s2")]
    assert len(s1_puts) == 1
    assert s1_puts[0]["body"]["content"] == "S1 edited content"
    assert len(s2_puts) == 0


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_scenario_5_slow_backend_response_resolves_safely():
    """Scenario 5: Slow backend response.

    Delayed PUT response resolves safely without race conditions.
    """
    data = _run_lifecycle_scenario("scenario_5")
    assert data["stateDuringFlight"] == "saving"
    assert data["stateAfterFlight"] == "saved"
    assert data["badgeText"] == "v3"


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_scenario_6_failed_backend_response_and_retry_recovery():
    """Scenario 6: Failed backend response.

    Transitions to 'error', retryable via subsequent edit and save.
    """
    data = _run_lifecycle_scenario("scenario_6")
    assert data["stateAfterFailure"] == "error"
    assert data["stateAfterEdit"] == "dirty"
    assert data["stateAfterSuccess"] == "saved"
    assert data["attempts"] == 2


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_scenario_7_multiple_edits_during_queued_saves_preserves_dirty():
    """Scenario 7: Multiple edits during queued saves.

    Verifies _editRevision vs saveRevision dirty preservation when edits happen
    during an in-flight save.
    """
    data = _run_lifecycle_scenario("scenario_7")
    assert data["savedBodies"] == ["first in-flight edit", "second concurrent edit"]
    assert data["stateAfterFirstCompleted"] == "dirty"
    assert data["stateAfterSecondCompleted"] == "saved"


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_scenario_8_document_deletion_cancels_autosave_and_avoids_orphans():
    """Scenario 8: Document deletion during pending work.

    Verifies no orphan PUT is sent or error shown when doc is deleted while autosave is debounced.
    """
    data = _run_lifecycle_scenario("scenario_8")
    assert data["deleteReceived"] is True
    assert data["putsCount"] == 0


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_scenario_9_correct_save_state_transitions():
    """Scenario 9: Correct save-state transitions.

    Verifies transitions between 'saved', 'dirty', 'saving', and back to 'saved'.
    """
    data = _run_lifecycle_scenario("scenario_9")
    assert data["sInitial"] == "saved"
    assert data["sDirty"] == "dirty"
    assert data["sSaving"] == "saving"
    assert data["sSaved"] == "saved"


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_scenario_10_no_updates_to_wrong_document():
    """Scenario 10: No updates to the wrong document.

    Verifies targeting docId 'target-b' while 'target-a' is active does not steal
    target-a's textarea contents.
    """
    data = _run_lifecycle_scenario("scenario_10")
    target_b_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/target-b")]
    assert len(target_b_puts) == 1
    assert target_b_puts[0]["body"]["content"] == "Doc B Stored Content"


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_deletion_http_500_then_save_and_retry():
    """HTTP 500 on delete keeps doc savable and allows retrying deletion later."""
    data = _run_lifecycle_scenario("deletion_http_500_then_save_and_retry")
    assert data["deleteAttempts"] == 2
    assert data["stillOpenAfter500"] is True
    assert data["tabExistsAfter500"] is True
    assert data["manualSaveOk"] is True
    assert [p["content"] for p in data["puts"]] == [
        "saved after failed delete",
        "autosaved after failed delete",
    ]
    assert data["tabExistsAfterSuccess"] is False
    assert data["saveAfterSuccess"] is False
    assert data["activeDocAfterSuccess"] is None


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_deletion_network_failure_preserves_doc_and_saves():
    """Network abort during delete leaves document active and savable."""
    data = _run_lifecycle_scenario("deletion_network_failure_preserves_doc_and_saves")
    assert data["deleteAborted"] is True
    assert data["docIdStillPresent"] is True
    assert data["savedAfterAbort"] is True
    assert len(data["puts"]) == 1
    assert data["puts"][0]["content"] == "saved after network abort"


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_pending_save_during_deletion_outcomes():
    """Verify pending saves during deletion: suppressed on 200, resumed on 500."""
    data = _run_lifecycle_scenario("pending_save_during_deletion_outcomes")
    assert data["saveDuringOkDelete"] is False
    ok_puts = [p for p in data["puts"] if p["url"].endswith("/del-ok-doc")]
    fail_puts = [p for p in data["puts"] if p["url"].endswith("/del-fail-doc")]
    assert len(ok_puts) == 0
    assert len(fail_puts) >= 1
    assert fail_puts[-1]["content"] == "pending edit preserved on fail"


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_pdf_pane_delayed_autosave_targets_originating_doc_after_tab_switch():
    """Delayed PDF form/annotation edits save to originating doc, not newly active tab."""
    data = _run_lifecycle_scenario("pdf_pane_delayed_autosave_targets_originating_doc_after_tab_switch")
    assert data["renderedDocWhenOnB"] == "pdf-b"
    assert data["fieldBValue"] == "Bob Original"
    assert "Alice" not in data["textareaB"]
    assert "Bob Original" in data["textareaB"]
    assert data["retainedDetachedRefs"] is False
    assert data["inputAAfterReturn"] == "Alice FormValue"
    pdf_a_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/pdf-a")]
    pdf_b_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/pdf-b")]
    assert len(pdf_a_puts) == 1
    assert "Alice FormValue" in pdf_a_puts[-1]["body"]["content"]
    assert "Alice DelayedTimer" not in pdf_a_puts[-1]["body"]["content"]
    assert len(pdf_b_puts) == 0


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_pdf_pane_autosave_across_session_switch_and_revision_serialization():
    """Session change while PDF save is debounced persists to originating doc and serializes PUTs."""
    data = _run_lifecycle_scenario("pdf_pane_autosave_across_session_switch_and_revision_serialization")
    assert data["activeDocAfterSwitch"] == "doc-sess-2"
    assert data["activeTextareaAfterSwitch"] == "Session Two Clean"
    s1_starts = [e for e in data["putEvents"] if e["phase"] == "start" and e["url"].endswith("/pdf-s1")]
    s2_puts = [e for e in data["putEvents"] if e["url"].endswith("/doc-sess-2")]
    assert len(s2_puts) == 0
    assert len(s1_starts) == 2
    assert "First Draft" in s1_starts[0]["content"]
    assert "Session One Final" in s1_starts[1]["content"]
    # Strict per-doc serialization: second PUT starts only after first PUT completes
    end_1 = next(i for i, e in enumerate(data["putEvents"]) if e["phase"] == "end" and e["seq"] == 1)
    start_2 = next(i for i, e in enumerate(data["putEvents"]) if e["phase"] == "start" and e["seq"] == 2)
    assert end_1 < start_2


@pytest.mark.usefixtures("_lifecycle_browser_session")
def test_pdf_pane_annotation_undo_and_export_preserved():
    """PDF form/annotation save, undo (Ctrl+Z), and export flush remain intact."""
    data = _run_lifecycle_scenario("pdf_pane_annotation_undo_and_export_preserved")
    assert data["beforeUndoCount"] == 1
    assert data["requests"][0]["type"] == "PUT"
    assert "Note One" in data["requests"][0]["content"]
    assert any(r["type"] == "EXPORT" for r in data["requests"])
    first_export = next(i for i, r in enumerate(data["requests"]) if r["type"] == "EXPORT")
    assert first_export >= 1
    assert data["afterUndoText"] == ""
    assert data["afterSecondUndoCount"] == 0


@pytest.mark.skipif(_IS_WINDOWS, reason="POSIX-specific orphan process group probe")
def test_worker_cleanup_removes_descendants_when_parent_dies_abruptly(tmp_path):
    """Verify orphan descendants terminate, including where PID 1 does not reap zombies."""
    pid_file = tmp_path / "descendant.pid"
    code = (
        "import os, subprocess, sys, time\n"
        "child = subprocess.Popen(['sleep', '30'])\n"
        "with open(sys.argv[1], 'w') as f: f.write(str(child.pid))\n"
        "sys.stdout.write('{\"status\": \"ready\"}\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(0.05)\n"
        "os._exit(0)\n"
    )
    worker = LifecycleBrowserWorker(cmd=[sys.executable, "-c", code, str(pid_file)])

    def descendant_running() -> bool:
        pid = int(pid_file.read_text())
        if sys.platform.startswith("linux"):
            try:
                # In a container without an init reaper, a terminated orphan can
                # remain as a zombie. killpg(pgid, 0) does not distinguish it.
                stat = Path(f"/proc/{pid}/stat").read_text()
            except FileNotFoundError:
                return False
            return stat.rsplit(") ", 1)[1].split()[0] not in {"Z", "X", "x"}
        try:
            os.kill(pid, 0)
            return True
        except ProcessLookupError:
            return False

    try:
        worker.start()
        proc = worker._proc

        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                break
            time.sleep(0.02)
        assert proc.poll() is not None, "Parent process should have exited"

        assert descendant_running(), "Descendant should be running before worker cleanup"

        worker.close()

        assert worker._proc is None
        assert worker._pgid is None
        assert proc.poll() is not None

        deadline = time.monotonic() + 2.0
        while descendant_running() and time.monotonic() < deadline:
            time.sleep(0.02)
        assert not descendant_running(), "Descendant should terminate during worker.close()"
    finally:
        worker.close()


def test_worker_shutdown_is_idempotent_across_competing_triggers():
    """Verify Node worker shutdown is idempotent across competing close triggers."""
    harness = """
import readline from 'node:readline';
import { createWorkerShutdown } from './tests/helpers/document_persistence_lifecycle_runner.mjs';

let rl;
let browserCloseCount = 0;
const fakeBrowser = {
  close: async () => {
    browserCloseCount += 1;
    await new Promise(r => setTimeout(r, 10));
  }
};

let samePromise = false;

const shutdown = createWorkerShutdown({
  browser: fakeBrowser,
  getRl: () => rl,
  onExit: code => {
    process.stdout.write(JSON.stringify({ ok: true, browserCloseCount, samePromise, exitCode: code }) + '\\n');
    process.exit(code);
  }
});

process.on('SIGTERM', () => { shutdown(0); });
process.on('SIGINT', () => { shutdown(0); });

rl = readline.createInterface({
  input: process.stdin,
  output: process.stdout,
  terminal: false,
});

rl.on('close', () => {
  shutdown(0);
});

rl.on('line', async line => {
  const msg = JSON.parse(line.trim());
  if (msg.action === 'close') {
    const p1 = shutdown(0);
    const p2 = shutdown(0);
    samePromise = (p1 === p2);
    process.emit('SIGTERM');
    rl.close();
    await Promise.all([p1, p2]);
  }
});

process.stdout.write(JSON.stringify({ status: 'ready' }) + '\\n');
"""
    worker = LifecycleBrowserWorker(
        cmd=["node", "--input-type=module", "-e", harness],
        startup_timeout=5.0,
    )
    try:
        worker.start()
        saved_proc = worker._proc
        result = worker.send_command({"action": "close"}, timeout=5.0)

        assert result.get("ok") is True
        assert result["browserCloseCount"] == 1, (
            f"Expected exactly 1 browser.close() invocation, got {result['browserCloseCount']}"
        )
        assert result["samePromise"] is True, "Expected repeated shutdown calls to share the identical promise"
        assert result["exitCode"] == 0

        saved_proc.wait(timeout=2.0)
        assert saved_proc.returncode == 0
    finally:
        worker.close()


def test_windows_worker_spawn_uses_process_group_without_posix_session(monkeypatch):
    """Windows starts a process group without invoking POSIX session management."""
    import io

    class FakeProcess:
        pid = 4321

        def __init__(self):
            self.stdin = io.StringIO()
            self.stdout = io.StringIO('{"status": "ready"}\n')
            self.stderr = io.StringIO()
            self.alive = True

        def poll(self):
            return None if self.alive else 0

        def wait(self, timeout=None):
            self.alive = False
            return 0

    kwargs_seen = []

    def fake_popen(*args, **kwargs):
        kwargs_seen.append(kwargs)
        return FakeProcess()

    monkeypatch.setattr(sys.modules[__name__], "_IS_WINDOWS", True)
    monkeypatch.setattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x200, raising=False)
    monkeypatch.setattr(subprocess, "Popen", fake_popen)

    worker = LifecycleBrowserWorker()
    try:
        worker.start()
        assert kwargs_seen[0]["creationflags"] == 0x200
        assert kwargs_seen[0]["start_new_session"] is False
        assert worker._pgid is None
    finally:
        worker.close()


def test_windows_worker_cleanup_terminates_process_tree(monkeypatch):
    """Windows cleanup requests taskkill /T and does not use POSIX process groups."""
    class FakeProcess:
        pid = 4321

        def __init__(self):
            self.alive = True

        def poll(self):
            return None if self.alive else 0

        def kill(self):
            self.alive = False

        def wait(self, timeout=None):
            return 0

    proc = FakeProcess()
    commands = []

    def fake_run(command, **kwargs):
        commands.append(command)
        assert kwargs["check"] is False
        assert kwargs["timeout"] == 5
        proc.alive = False

    monkeypatch.setattr(sys.modules[__name__], "_IS_WINDOWS", True)
    monkeypatch.setattr(subprocess, "run", fake_run)

    worker = LifecycleBrowserWorker()
    worker._terminate_process_group(proc=proc)

    assert commands == [["taskkill", "/PID", "4321", "/T", "/F"]]
    assert proc.poll() == 0


def test_worker_timeout_fails_deterministically_and_cleans_up():
    """Verify worker timeouts fail with an actionable error and clean up processes."""
    hang_code = (
        "import os, sys, time\n"
        "sys.stdout.write('{\"status\": \"ready\"}\\n')\n"
        "sys.stdout.flush()\n"
        "time.sleep(30)\n"
    )
    worker = LifecycleBrowserWorker(
        cmd=[sys.executable, "-c", hang_code],
        startup_timeout=5.0,
    )
    try:
        worker.start()
        saved_pgid = worker._pgid
        saved_proc = worker._proc

        with pytest.raises(TimeoutError) as exc_info:
            worker.send_command({"action": "test_hang"}, timeout=0.6)

        assert "timed out after 0.6s without response" in str(exc_info.value)
        assert worker._proc is None
        assert worker._pgid is None

        assert saved_proc.poll() is not None
        if not _IS_WINDOWS:
            try:
                os.killpg(saved_pgid, 0)
                still_alive = True
            except (ProcessLookupError, OSError):
                still_alive = False
            assert still_alive is False, "Hanging worker process should have been terminated on timeout"
    finally:
        worker.close()
