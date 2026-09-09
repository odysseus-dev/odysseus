from __future__ import annotations

import ast
from pathlib import Path

import yaml

_ROOT = Path(__file__).resolve().parent.parent
_LEDGER = yaml.safe_load((_ROOT / "config" / "agents" / "legacy-disposition.yaml").read_text(encoding="utf-8"))
_PRODUCTION = (_ROOT / "routes", _ROOT / "src", _ROOT / "app.py")
_FORBIDDEN = {"stream_agent_loop", "execute_tool_block"}
_ALLOWED_RESIDUAL_FILES = {
    "src/agent_loop.py",
    "src/tool_execution.py",
    "src/agent_tools/__init__.py",
}


def _iter_py(root: Path):
    if root.is_file():
        yield root
        return
    for path in root.rglob("*.py"):
        yield path


def _calls(path: Path) -> set[str]:
    found: set[str] = set()
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            func = node.func
            name = getattr(func, "id", None) or getattr(func, "attr", None)
            if name in _FORBIDDEN:
                found.add(name)
    return found


def test_ledger_covers_every_legacy_symbol():
    required = {"old_symbol", "location", "purpose", "target", "execution_kind", "owner", "replacement_test", "deletion_status"}
    for entry in _LEDGER["entries"]:
        assert required <= set(entry)
        assert entry["deletion_status"]


def test_production_has_no_unledgered_legacy_calls():
    listed = {(entry["location"], entry["old_symbol"]) for entry in _LEDGER["entries"]}
    unknown: list[str] = []
    for root in _PRODUCTION:
        for path in _iter_py(root):
            rel = str(path.relative_to(_ROOT))
            if rel.startswith("src/agent_loop") or rel.startswith("tests/"):
                continue
            for symbol in _calls(path):
                if (rel, symbol) not in listed and rel not in _ALLOWED_RESIDUAL_FILES:
                    unknown.append(f"{rel}:{symbol}")
    assert unknown == []


def test_legacy_loop_is_held_until_acceptance_ledger_says_so():
    residuals = [entry for entry in _LEDGER["entries"] if entry["deletion_status"] == "residual-until-acceptance"]
    assert residuals
    for entry in residuals:
        assert (_ROOT / entry["location"]).is_file()


def test_five_loop_callers_no_longer_invoke_stream_agent_loop():
    for rel in (
        "routes/chat_routes.py",
        "routes/skills_routes.py",
        "src/teacher_escalation.py",
        "src/bg_monitor.py",
        "src/task_scheduler.py",
    ):
        assert "stream_agent_loop" not in _calls(_ROOT / rel)
