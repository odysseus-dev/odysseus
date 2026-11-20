"""Automated regression coverage for document persistence and lifecycle.

Validates autosave document identity, closing active/inactive tabs, session
switching isolation, slow and failed backend responses, queue serialization
with concurrent edits, document deletion cancellation, save-state transitions,
and prevention of cross-document overwrites.
"""

import atexit
import json
import os
import subprocess
from pathlib import Path

import pytest

from tests.helpers.stylesheets import stylesheet_link_tags

ROOT = Path(__file__).resolve().parents[1]


class LifecycleBrowserWorker:
    """Persistent Node and Playwright worker managing isolated browser contexts."""

    def __init__(self):
        self._proc = None

    def start(self):
        if self._proc is not None and self._proc.poll() is None:
            return
        runner_path = ROOT / "tests" / "helpers" / "document_persistence_lifecycle_runner.mjs"
        env = os.environ.copy()
        self._proc = subprocess.Popen(
            ["node", str(runner_path), "--worker"],
            cwd=ROOT,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            env=env,
        )
        ready_line = self._proc.stdout.readline()
        if not ready_line:
            stderr = self._proc.stderr.read()
            raise RuntimeError(f"Failed to start persistence browser worker:\n{stderr}")
        ready_data = json.loads(ready_line)
        if ready_data.get("status") != "ready":
            raise RuntimeError(f"Unexpected worker handshake: {ready_line}")

    def run_scenario(self, scenario_name: str) -> dict:
        self.start()
        req = json.dumps({"scenario": scenario_name}) + "\n"
        try:
            self._proc.stdin.write(req)
            self._proc.stdin.flush()
            resp_line = self._proc.stdout.readline()
        except (BrokenPipeError, OSError):
            self.close()
            self.start()
            self._proc.stdin.write(req)
            self._proc.stdin.flush()
            resp_line = self._proc.stdout.readline()

        if not resp_line:
            stderr = self._proc.stderr.read()
            self.close()
            raise RuntimeError(
                f"Worker closed stdout unexpectedly while running {scenario_name}:\n{stderr}"
            )
        resp = json.loads(resp_line)
        if not resp.get("ok"):
            raise RuntimeError(
                f"Scenario {scenario_name} failed in browser runner:\n{resp.get('error')}"
            )
        return resp["data"]

    def close(self):
        if self._proc is None:
            return
        try:
            if self._proc.poll() is None and self._proc.stdin:
                self._proc.stdin.write(json.dumps({"action": "close"}) + "\n")
                self._proc.stdin.flush()
                self._proc.wait(timeout=3)
        except Exception:
            try:
                self._proc.kill()
            except Exception:
                pass
        finally:
            self._proc = None


_worker = None


def _get_worker() -> LifecycleBrowserWorker:
    global _worker
    if _worker is None:
        _worker = LifecycleBrowserWorker()
        atexit.register(_worker.close)
    return _worker


@pytest.fixture(scope="module", autouse=True)
def _lifecycle_browser_session():
    worker = _get_worker()
    worker.start()
    try:
        yield worker
    finally:
        worker.close()


def _run_lifecycle_scenario(scenario_name: str) -> dict:
    return _get_worker().run_scenario(scenario_name)


def _run_browser_test(js_script: str) -> dict:
    """Fallback runner for standalone raw JavaScript snippets."""
    full_script = js_script.replace("__ODY_STYLESHEETS__", stylesheet_link_tags())
    result = subprocess.run(
        ["node", "--input-type=module", "-e", full_script],
        cwd=ROOT,
        check=False,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, (
        f"Node runner failed with exit code {result.returncode}\n"
        f"STDOUT:\n{result.stdout}\n"
        f"STDERR:\n{result.stderr}"
    )
    return json.loads(result.stdout)


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


def test_scenario_5_slow_backend_response_resolves_safely():
    """Scenario 5: Slow backend response.

    Delayed PUT response resolves safely without race conditions.
    """
    data = _run_lifecycle_scenario("scenario_5")
    assert data["stateDuringFlight"] == "saving"
    assert data["stateAfterFlight"] == "saved"
    assert data["badgeText"] == "v3"


def test_scenario_6_failed_backend_response_and_retry_recovery():
    """Scenario 6: Failed backend response.

    Transitions to 'error', retryable via subsequent edit and save.
    """
    data = _run_lifecycle_scenario("scenario_6")
    assert data["stateAfterFailure"] == "error"
    assert data["stateAfterEdit"] == "dirty"
    assert data["stateAfterSuccess"] == "saved"
    assert data["attempts"] == 2


def test_scenario_7_multiple_edits_during_queued_saves_preserves_dirty():
    """Scenario 7: Multiple edits during queued saves.

    Verifies _editRevision vs saveRevision dirty preservation when edits happen
    during an in-flight save.
    """
    data = _run_lifecycle_scenario("scenario_7")
    assert data["savedBodies"] == ["first in-flight edit", "second concurrent edit"]
    assert data["stateAfterFirstCompleted"] == "dirty"
    assert data["stateAfterSecondCompleted"] == "saved"


def test_scenario_8_document_deletion_cancels_autosave_and_avoids_orphans():
    """Scenario 8: Document deletion during pending work.

    Verifies no orphan PUT is sent or error shown when doc is deleted while autosave is debounced.
    """
    data = _run_lifecycle_scenario("scenario_8")
    assert data["deleteReceived"] is True
    assert data["putsCount"] == 0


def test_scenario_9_correct_save_state_transitions():
    """Scenario 9: Correct save-state transitions.

    Verifies transitions between 'saved', 'dirty', 'saving', and back to 'saved'.
    """
    data = _run_lifecycle_scenario("scenario_9")
    assert data["sInitial"] == "saved"
    assert data["sDirty"] == "dirty"
    assert data["sSaving"] == "saving"
    assert data["sSaved"] == "saved"


def test_scenario_10_no_updates_to_wrong_document():
    """Scenario 10: No updates to the wrong document.

    Verifies targeting docId 'target-b' while 'target-a' is active does not steal
    target-a's textarea contents.
    """
    data = _run_lifecycle_scenario("scenario_10")
    target_b_puts = [p for p in data["puts"] if p["url"].endswith("/api/document/target-b")]
    assert len(target_b_puts) == 1
    assert target_b_puts[0]["body"]["content"] == "Doc B Stored Content"


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


def test_deletion_network_failure_preserves_doc_and_saves():
    """Network abort during delete leaves document active and savable."""
    data = _run_lifecycle_scenario("deletion_network_failure_preserves_doc_and_saves")
    assert data["deleteAborted"] is True
    assert data["docIdStillPresent"] is True
    assert data["savedAfterAbort"] is True
    assert len(data["puts"]) == 1
    assert data["puts"][0]["content"] == "saved after network abort"


def test_pending_save_during_deletion_outcomes():
    """Verify pending saves during deletion: suppressed on 200, resumed on 500."""
    data = _run_lifecycle_scenario("pending_save_during_deletion_outcomes")
    assert data["saveDuringOkDelete"] is False
    ok_puts = [p for p in data["puts"] if p["url"].endswith("/del-ok-doc")]
    fail_puts = [p for p in data["puts"] if p["url"].endswith("/del-fail-doc")]
    assert len(ok_puts) == 0
    assert len(fail_puts) >= 1
    assert fail_puts[-1]["content"] == "pending edit preserved on fail"


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
