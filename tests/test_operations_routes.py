import json
import shutil
import subprocess
from pathlib import Path

import pytest

from routes import operations_routes as ops
from src import project_operations


def _git(path: Path, *args: str):
    return subprocess.run(
        [shutil.which("git"), *args],
        cwd=path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )


def test_project_config_round_trip_stays_inside_workspace(tmp_path):
    saved = ops._atomic_save_project_config(
        str(tmp_path),
        {
            "instructions": "Use PowerShell and run focused tests.",
            "test_command": "pytest -q",
            "protected_paths": [".env"],
            "permission_rules": ["Ask before pushing"],
            "checkpoint_before_changes": True,
            "visual_qa_url": "http://127.0.0.1:7000",
            "github_base_branch": "dev",
            "context_compaction_percent": 82,
        },
    )

    config_path = tmp_path / ".odysseus" / "project.json"
    assert Path(saved["config_path"]) == config_path.resolve()
    assert saved["instructions"].startswith("Use PowerShell")
    assert saved["permission_rules"] == ["Ask before pushing"]
    assert saved["context_compaction_percent"] == 82
    assert json.loads(config_path.read_text(encoding="utf-8"))["github_base_branch"] == "dev"


def test_project_rules_enter_prompt_and_protected_paths_are_enforced(tmp_path, monkeypatch):
    project_operations.save_project_config(str(tmp_path), {
        "instructions": "Prefer the service boundary.",
        "test_command": "pytest tests/test_service.py -q",
        "protected_paths": ["secrets/**", ".env"],
        "permission_rules": ["Ask before pushing"],
        "checkpoint_before_changes": False,
        "visual_qa_url": "http://127.0.0.1:7000",
    })

    from src.agent_loop import _workspace_coding_rules
    from src import tool_execution

    prompt = _workspace_coding_rules(str(tmp_path))
    assert "Prefer the service boundary." in prompt
    assert "pytest tests/test_service.py -q" in prompt
    assert "Ask before pushing" in prompt

    token = tool_execution._active_workspace.set(str(tmp_path))
    try:
        blocked = tool_execution._project_tool_guard(
            "write_file",
            json.dumps({"path": "secrets/token.txt", "content": "nope"}),
            "session-1",
        )
    finally:
        tool_execution._active_workspace.reset(token)
    assert blocked["policy"] == "project_protected_path"


def test_project_guard_checkpoints_only_mutating_commands(tmp_path, monkeypatch):
    project_operations.save_project_config(str(tmp_path), {
        "checkpoint_before_changes": True,
    })
    created = []
    monkeypatch.setattr(project_operations, "create_checkpoint", lambda workspace, label: created.append(label) or {"id": "cp"})

    from src import tool_execution

    token = tool_execution._active_workspace.set(str(tmp_path))
    try:
        assert tool_execution._project_tool_guard("bash", "git status --short", "s1") is None
        assert created == []
        assert tool_execution._project_tool_guard("edit_file", '{"path":"app.py"}', "s1") is None
    finally:
        tool_execution._active_workspace.reset(token)
    assert created == ["auto-edit_file-s1"]


def test_runtime_snapshot_reports_loaded_ollama_context(monkeypatch, tmp_path):
    def fake_probe(url, timeout=1.5):
        if url.endswith("/api/version"):
            return True, {"version": "9.9.9"}, "", 4
        if url.endswith("/api/ps"):
            return True, {"models": [{
                "name": "qwen3.5-9b-96k:latest",
                "size": 10,
                "size_vram": 8,
                "context_length": 98_304,
                "details": {"quantization_level": "Q4_K_M"},
            }]}, "", 3
        return False, {}, "not running", 2

    monkeypatch.setattr(ops, "_probe_json", fake_probe)
    monkeypatch.setattr(ops, "_gpu_snapshot", lambda: [])
    snapshot = ops._runtime_snapshot(str(tmp_path))

    ollama = next(service for service in snapshot["services"] if service["id"] == "ollama")
    assert ollama["status"] == "healthy"
    assert snapshot["ollama"]["version"] == "9.9.9"
    assert snapshot["ollama"]["loaded_models"][0]["context_length"] == 98_304
    assert next(service for service in snapshot["services"] if service["id"] == "chroma")["status"] == "degraded"


@pytest.mark.skipif(not shutil.which("git"), reason="git is required")
def test_checkpoint_is_non_destructive_and_listed(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Odysseus Test")
    tracked = tmp_path / "tracked.txt"
    tracked.write_text("before\n", encoding="utf-8")
    _git(tmp_path, "add", "tracked.txt")
    _git(tmp_path, "commit", "-m", "initial")
    tracked.write_text("after\n", encoding="utf-8")

    checkpoint = ops._create_checkpoint(str(tmp_path), "before risky edit")

    assert tracked.read_text(encoding="utf-8") == "after\n"
    assert checkpoint["tracked_only"] is True
    assert any(item["id"] == checkpoint["id"] for item in ops._list_checkpoints(str(tmp_path)))


@pytest.mark.skipif(not shutil.which("git"), reason="git is required")
def test_worktree_parser_reports_branch_and_path(tmp_path):
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.email", "test@example.com")
    _git(tmp_path, "config", "user.name", "Odysseus Test")
    (tmp_path / "readme.txt").write_text("hello\n", encoding="utf-8")
    _git(tmp_path, "add", "readme.txt")
    _git(tmp_path, "commit", "-m", "initial")

    rows = ops._worktrees(str(tmp_path))

    assert len(rows) == 1
    assert Path(rows[0]["path"]).resolve() == tmp_path.resolve()
    assert rows[0]["branch"]


def test_mission_control_is_wired_into_app_and_activity():
    root = Path(__file__).resolve().parents[1]
    app = (root / "app.py").read_text(encoding="utf-8")
    frontend = (root / "static" / "app.js").read_text(encoding="utf-8")
    activity = (root / "static" / "js" / "activity-center.js").read_text(encoding="utf-8")
    mission = (root / "static" / "js" / "mission-control.js").read_text(encoding="utf-8")

    assert "setup_operations_routes(session_manager)" in app
    assert "mission-control.js?v=20260823mission2" in frontend
    assert "missionControlModule.init()" in frontend
    assert "Open Mission Control" in activity
    assert "/api/operations/review" in mission
    assert "/api/operations/runtime" in mission
    assert "/api/operations/context" in mission
    assert "/api/operations/checkpoints/restore" in mission
    assert "Delivery cockpit" in mission
    assert "Background agent queue" in mission
