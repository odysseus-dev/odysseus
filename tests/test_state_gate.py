"""StateGate blocks sensitive paths even when no config file exists."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from src import state_gate


@pytest.fixture
def gate(tmp_path, monkeypatch):
    protected = tmp_path / "protected"
    protected.mkdir()
    cfg = {
        "enabled": True,
        "protected": [str(protected)],
        "allow_deploy": False,
        "max_mutations_per_turn": 2,
    }
    path = tmp_path / "state-gate.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    monkeypatch.setattr(state_gate, "CONFIG", path)
    state_gate.reset_turn()
    yield protected
    state_gate.reset_turn()


def test_default_blocks_ssh_without_a_config_file(monkeypatch, tmp_path):
    monkeypatch.setattr(state_gate, "CONFIG", tmp_path / "missing.json")
    state_gate.reset_config_cache()
    assert state_gate.check_path(str(tmp_path / "nope"), action="write") is None
    err = state_gate.check_path("/etc/passwd", action="write")
    assert err and "blocked" in err
    bash = state_gate.check_bash("rm -rf ~/.ssh/id_rsa")
    assert bash and "protected" in bash


def test_config_can_turn_the_gate_off(monkeypatch, tmp_path):
    cfg = tmp_path / "state-gate.json"
    cfg.write_text(json.dumps({"enabled": False}), encoding="utf-8")
    monkeypatch.setattr(state_gate, "CONFIG", cfg)
    state_gate.reset_config_cache()
    assert state_gate.check_path("/etc/passwd", action="edit") is None
    assert state_gate.check_bash("rm /etc/passwd") is None


def test_config_can_add_a_path(monkeypatch, tmp_path):
    extra = tmp_path / "vault"
    extra.mkdir()
    cfg = tmp_path / "state-gate.json"
    cfg.write_text(json.dumps({"protected": [str(extra)]}), encoding="utf-8")
    monkeypatch.setattr(state_gate, "CONFIG", cfg)
    state_gate.reset_config_cache()
    err = state_gate.check_path(str(extra / "note.txt"), action="write")
    assert err and "blocked" in err
    assert state_gate.check_path("/etc/shadow", action="write")


def test_protected_path_is_blocked(gate):
    err = state_gate.check_path(str(gate / "secret.txt"))
    assert err and "protected path" in err


def test_home_relative_protected_path_is_blocked(gate):
    err = state_gate.check_path("~/.ssh/id_ed25519")
    assert err and "protected path" in err


def test_similar_prefix_is_not_protected(gate):
    sibling = gate.parent / (gate.name + "-public")
    sibling.mkdir()
    assert state_gate.check_path(str(sibling / "ok.txt")) is None


def test_bash_mutation_against_protected_path_is_blocked(gate):
    err = state_gate.check_bash(f"rm -rf {gate}/x")
    assert err and "command blocked" in err


def test_bash_without_mutation_passes(gate):
    assert state_gate.check_bash("ls -la /tmp") is None


def test_bash_sed_in_place_is_detected(gate):
    err = state_gate.check_bash(f"sed -i 's/a/b/' {gate}/x")
    assert err and "command blocked" in err


def test_bash_mutation_behind_wrappers_is_detected(gate):
    """sudo/env/xargs/nice must not hide the mutation verb."""
    for command in (
        f"sudo rm -rf {gate}/x",
        f"env A=1 rm -rf {gate}/x",
        f"nice -n 5 rm -rf {gate}/x",
        f"printf x | xargs rm -rf {gate}/x",
    ):
        err = state_gate.check_bash(command)
        assert err and "command blocked" in err, command


def test_bash_path_traversal_into_protected_is_blocked(gate):
    err = state_gate.check_bash(f"rm -rf {gate.parent}/x/../{gate.name}/secret.txt")
    assert err and "command blocked" in err


def test_bash_read_of_protected_path_is_allowed(gate):
    assert state_gate.check_bash(f"cat {gate}/secret.txt") is None


def test_protected_sibling_name_is_not_a_match(gate):
    sibling = gate.parent / (gate.name + "-public")
    sibling.mkdir()
    assert state_gate.check_bash(f"rm -rf {sibling}/ok.txt") is None


def test_inline_interpreter_writing_protected_is_blocked(gate):
    err = state_gate.check_bash(
        f"python3 -c \"open('{gate}/secret.txt','w').write('x')\""
    )
    assert err and "command blocked" in err


def test_inline_interpreter_without_protected_path_passes(gate):
    assert state_gate.check_bash("python3 -c 'print(1+1)'") is None


def test_counter_is_per_request_and_resets(gate):
    assert state_gate.check_path("/tmp/a", ctx={"request_id": "r1"}) is None
    assert state_gate.check_path("/tmp/b", ctx={"request_id": "r1"}) is None
    overflow = state_gate.check_path("/tmp/c", ctx={"request_id": "r1"})
    assert overflow and "limit of 2" in overflow
    assert state_gate.check_path("/tmp/d", ctx={"request_id": "r2"}) is None


def test_deploy_is_blocked_by_default(gate):
    err = state_gate.check_path("/tmp/release", action="deploy")
    assert err and "allow_deploy" in err


def test_config_is_cached_and_reloaded_on_change(tmp_path, monkeypatch):
    path = tmp_path / "cached.json"
    path.write_text(json.dumps({"enabled": True, "protected": []}), encoding="utf-8")
    monkeypatch.setattr(state_gate, "CONFIG", path)
    state_gate.reset_config_cache()
    first = state_gate.config()
    assert state_gate.config() is first  # same object: it did not re-read
    path.write_text(json.dumps({"enabled": False, "protected": ["/x"], "extra": 1}), encoding="utf-8")
    reloaded = state_gate.config()
    assert reloaded["enabled"] is False
    assert state_gate.check_path("/etc/passwd") is None


def test_every_state_gate_symbol_imported_elsewhere_exists():
    """Regression: a lazy import of a removed helper aborted a whole run."""
    import ast

    root = Path(__file__).resolve().parents[1]
    wanted = set()
    for pattern in ("src/**/*.py", "routes/**/*.py"):
        for path in root.glob(pattern):
            tree = ast.parse(path.read_text(encoding="utf-8", errors="ignore"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module in {
                    "src.state_gate",
                    "state_gate",
                }:
                    wanted.update(alias.name for alias in node.names)

    missing = sorted(name for name in wanted if not hasattr(state_gate, name))
    assert not missing, f"src.state_gate does not expose: {missing}"
