"""Regression guards for Cookbook port parsing / collision logic (#4507)."""
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNNING = (ROOT / "static/js/cookbookRunning.js").read_text(encoding="utf-8")
HWFIT = (ROOT / "static/js/cookbook-hwfit.js").read_text(encoding="utf-8")
SERVE = (ROOT / "static/js/cookbookServe.js").read_text(encoding="utf-8")


def test_task_port_is_single_reader_and_handles_all_forms():
    # _taskPort must parse --port <n>, --port=<n>, and -p <n>
    assert r"cmd.match(/--port[=\s]+(\d+)/) || cmd.match(/(?:^|\s)-p[=\s]+(\d+)/)" in RUNNING
    # and it must be exported so the guards can share it
    assert "_taskPort }" in RUNNING or "_taskPort," in RUNNING.split("export {", 1)[-1]


def test_next_available_port_uses_shared_reader():
    assert "const p = _taskPort(t);" in RUNNING
    assert "if (p) usedPorts.add(parseInt(p));" in RUNNING


def test_guards_reuse_shared_reader_not_inline_regex():
    assert "_allServes.filter(t => _taskPort(t) === _qrPort)" in HWFIT
    assert "_active.filter(t => _runningMod._taskPort(t) === _newPort)" in SERVE


def test_quickrun_llamacpp_autoassigns_port():
    # no hardcoded 8080: port comes from _nextAvailablePort for every backend
    assert "const _qrPort = _nextAvailablePort();" in HWFIT
    assert "'llamacpp' ? '8080'" not in HWFIT
    assert "--port 8080" not in HWFIT
