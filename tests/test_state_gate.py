"""StateGate blocks sensitive paths even when no config file exists."""
import json

from src import state_gate


def test_default_blocks_ssh_without_a_config_file(monkeypatch, tmp_path):
    monkeypatch.setattr(state_gate, "CONFIG", tmp_path / "missing.json")
    blocked = state_gate.check_path(str(tmp_path / "nope"), action="write")
    assert blocked is None
    err = state_gate.check_path("/etc/passwd", action="write")
    assert err and "blocked" in err
    bash = state_gate.check_bash("rm -rf ~/.ssh/id_rsa")
    assert bash and "protected" in bash


def test_config_can_turn_the_gate_off(monkeypatch, tmp_path):
    cfg = tmp_path / "state-gate.json"
    cfg.write_text(json.dumps({"enabled": False}), encoding="utf-8")
    monkeypatch.setattr(state_gate, "CONFIG", cfg)
    assert state_gate.check_path("/etc/passwd", action="edit") is None
    assert state_gate.check_bash("rm /etc/passwd") is None


def test_config_can_add_a_path(monkeypatch, tmp_path):
    extra = tmp_path / "vault"
    extra.mkdir()
    cfg = tmp_path / "state-gate.json"
    cfg.write_text(json.dumps({"protected": [str(extra)]}), encoding="utf-8")
    monkeypatch.setattr(state_gate, "CONFIG", cfg)
    err = state_gate.check_path(str(extra / "note.txt"), action="write")
    assert err and "blocked" in err
    assert state_gate.check_path("/etc/shadow", action="write")
