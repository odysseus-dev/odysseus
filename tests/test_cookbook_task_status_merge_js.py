"""Regression tests for Cookbook background task status merging.

The Running tab is browser-heavy, so these tests exercise the pure merge helper
under Node. The #2193 bug was that background status polling kept the red error
badge but discarded backend-provided serve diagnosis and launch command details.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node binary not on PATH")


def _run_node(script: str) -> dict:
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if res.returncode != 0:
        raise AssertionError(f"node failed:\n{res.stderr}")
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    if not lines:
        raise AssertionError("node produced no stdout")
    return json.loads(lines[-1])


def test_background_status_persists_serve_diagnosis_and_command(node_available):
    script = textwrap.dedent("""
        const { applyLiveTaskStatus } = await import('./static/js/cookbookTaskStatusMerge.js');
        const task = {
          sessionId: 'serve-1',
          type: 'serve',
          status: 'running',
          output: '',
          payload: { repo_id: 'org/model' },
        };
        const changed = applyLiveTaskStatus(task, {
          session_id: 'serve-1',
          type: 'serve',
          status: 'error',
          output_tail: 'Traceback\\nCUDA runtime library not found',
          diagnosis: {
            message: 'CUDA runtime is missing.',
            suggestion: 'Suggested action: install CUDA runtime or use CPU fallback.',
          },
          cmd: 'vllm serve org/model --port 8000',
        });
        console.log(JSON.stringify({
          changed,
          status: task.status,
          output: task.output,
          diagnosis: task.diagnosis,
          cmd: task.payload._cmd,
        }));
    """)
    out = _run_node(script)
    assert out["changed"] is True
    assert out["status"] == "error"
    assert "CUDA runtime library not found" in out["output"]
    assert out["diagnosis"]["message"] == "CUDA runtime is missing."
    assert out["cmd"] == "vllm serve org/model --port 8000"


def test_background_status_clears_stale_diagnosis_when_serve_recovers(node_available):
    script = textwrap.dedent("""
        const { applyLiveTaskStatus } = await import('./static/js/cookbookTaskStatusMerge.js');
        const task = {
          sessionId: 'serve-2',
          type: 'serve',
          status: 'error',
          progress: '',
          diagnosis: { message: 'old failure' },
          payload: { _cmd: 'keep this command' },
        };
        const changed = applyLiveTaskStatus(task, {
          session_id: 'serve-2',
          type: 'serve',
          status: 'running',
          progress: 'loading 42%',
          cmd: 'do not overwrite existing command',
        });
        console.log(JSON.stringify({
          changed,
          status: task.status,
          progress: task.progress,
          diagnosis: task.diagnosis,
          cmd: task.payload._cmd,
        }));
    """)
    out = _run_node(script)
    assert out["changed"] is True
    assert out["status"] == "running"
    assert out["progress"] == "loading 42%"
    assert out["diagnosis"] is None
    assert out["cmd"] == "keep this command"


def test_background_status_does_not_duplicate_output_tail(node_available):
    script = textwrap.dedent("""
        const { applyLiveTaskStatus } = await import('./static/js/cookbookTaskStatusMerge.js');
        const task = {
          sessionId: 'download-1',
          type: 'download',
          status: 'running',
          output: 'line 1\\nline 2',
          payload: {},
        };
        const changed = applyLiveTaskStatus(task, {
          session_id: 'download-1',
          type: 'download',
          status: 'running',
          output_tail: 'line 2',
        });
        console.log(JSON.stringify({ changed, output: task.output }));
    """)
    out = _run_node(script)
    assert out["changed"] is False
    assert out["output"] == "line 1\nline 2"
