"""Per-call outcome guidance for interrupted turns (fifth Desktop/gateway patch).

Behavioural checks run the synthetic transcripts from resume_outcome_scenarios.py inside a patched Hermes tree
(HERMES_PATCHED_SRC) with the Hermes interpreter (HERMES_PATCHED_PYTHON). Nothing is executed or written by the scenarios."""
import json
import os
import subprocess
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATCH = ROOT / "integrations" / "hermes-mode" / "patches" / "webapp-resume-outcomes.patch"
SCEN = Path(__file__).with_name("resume_outcome_scenarios.py")
SRC = os.environ.get("HERMES_PATCHED_SRC")
PY = os.environ.get("HERMES_PATCHED_PYTHON")


def test_patch_is_narrow_and_leaves_other_patches_alone():
    text = PATCH.read_text(encoding="utf-8")
    files = {line.split(" b/")[-1] for line in text.splitlines() if line.startswith("diff --git")}
    assert files == {
        "agent/agent_runtime_helpers.py",
        "agent/interrupted_outcomes.py",
        "agent/replay_cleanup.py",
        "gateway/run.py",
        "gateway/run_turn_runner.py",
    }
    assert "skip any unfinished work" not in "".join(l[1:] for l in text.splitlines() if l.startswith("+") and not l.startswith("+++"))


@pytest.fixture(scope="module")
def out():
    if not (SRC and PY):
        pytest.skip("HERMES_PATCHED_SRC / HERMES_PATCHED_PYTHON not set")
    home = tempfile.mkdtemp(prefix="resume_scen_")
    env = {k: v for k, v in os.environ.items() if k != "PYTHONPATH"}
    r = subprocess.run([PY, str(SCEN), SRC, home], capture_output=True, text=True, env=env, timeout=180)
    line = next((l for l in r.stdout.splitlines() if l.startswith("@@")), None)
    assert line, r.stdout[-500:] + r.stderr[-800:]
    return json.loads(line[2:])


def _results(o, name):
    return {t["id"]: t for t in o[name]["tail"] if t["role"] == "tool"}


def test_file_write_without_result_is_unknown_not_skipped(out):
    note = out["file_write_no_result"]["note"]
    assert "UNKNOWN" in note and "write_file F:/p/a.py" in note and "re-read the target file" in note
    assert "skip any unfinished work" not in note
    assert _results(out, "file_write_no_result")["w1"]["disposition"] == "unknown"


def test_git_commit_that_may_exist_points_at_git_log(out):
    note = out["git_commit_no_result"]["note"]
    assert "git commit" in note and "git log -1" in note and "already happened" in note
    assert "Do not repeat an UNKNOWN call until you have verified" in note


def test_command_interrupted_before_exit_status_is_unknown(out):
    note = out["command_no_exit_status"]["note"]
    assert "npm run build" in note and "UNKNOWN" in note
    assert _results(out, "command_no_exit_status")["t2"]["disposition"] == "unknown"


def test_mixed_block_keeps_recorded_and_flags_only_unrecorded(out):
    res = _results(out, "partial_block_3_calls")
    assert res["a"]["content"] == '{"ok": true}' and res["a"]["disposition"] is None
    assert res["b"]["disposition"] == "unknown" and res["c"]["disposition"] == "unknown"
    note = out["partial_block_3_calls"]["note"]
    assert "b.py" in note and "git commit" in note
    assert "read_file" not in note.split("UNKNOWN", 1)[1].split("Calls that have a recorded result")[0]


def test_interrupted_read_only_call_never_demands_verification(out):
    note = out["readonly_no_result"]["note"]
    assert "UNKNOWN" not in note and "Do not repeat an UNKNOWN" not in note


def test_complete_turn_lists_nothing_and_preserves_results(out):
    note = out["all_answered"]["note"]
    assert "UNKNOWN" not in note and "could not be identified" not in note
    assert "do not repeat them" in note


def test_unidentifiable_call_is_reported_not_invented(out):
    note = out["unidentifiable_call"]["note"]
    assert "could not be identified individually" in note
    assert _results(out, "unidentifiable_call")["k"]["content"] == '{"ok": true}'


def test_transcript_stays_pair_consistent(out):
    for name, data in out.items():
        ids = [t["id"] for t in data["tail"] if t["role"] == "tool"]
        assert len(ids) == len(set(ids)), name
