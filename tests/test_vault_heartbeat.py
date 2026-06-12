"""Tests for scripts/vault_heartbeat.py (vault conformance heartbeat writer).

The payload contract is owned by the vault repo's fail-closed validator
(obsidian-PhD Scripts/automation/odysseus_heartbeat.py); these tests pin the
writer to that contract: required fields, LF-normalised allowlist hash,
bounded actions parser, dated filename, and fail-closed behaviour.
"""

import importlib.util
import json
import re
from pathlib import Path

import pytest

_SPEC = importlib.util.spec_from_file_location(
    "vault_heartbeat",
    Path(__file__).resolve().parents[1] / "scripts" / "vault_heartbeat.py",
)
vault_heartbeat = importlib.util.module_from_spec(_SPEC)
_SPEC.loader.exec_module(vault_heartbeat)

ALLOWLIST = """\
defaults:
  enabled: false

actions:
  research-engine-health:
    enabled: true
  task-transition:
    enabled: false
  routine-report-stage:
    enabled: false
"""

REQUIRED_FIELDS = (
    "schema_version",
    "report_kind",
    "generated_at",
    "service_version",
    "actions_yaml_sha256",
    "enabled_actions",
    "last_dispatch",
)


def _make_vault(tmp_path: Path, allowlist_text: str = ALLOWLIST) -> Path:
    actions = tmp_path / "automation" / "config" / "odysseus_actions.yaml"
    actions.parent.mkdir(parents=True)
    actions.write_text(allowlist_text, encoding="utf-8")
    return tmp_path


def test_sha256_normalises_crlf(tmp_path):
    lf = tmp_path / "lf.yaml"
    crlf = tmp_path / "crlf.yaml"
    lf.write_bytes(ALLOWLIST.encode("utf-8"))
    crlf.write_bytes(ALLOWLIST.replace("\n", "\r\n").encode("utf-8"))
    assert vault_heartbeat.actions_yaml_sha256(lf) == vault_heartbeat.actions_yaml_sha256(crlf)


def test_parser_reads_enabled_flags():
    actions = vault_heartbeat.parse_allowlisted_actions(ALLOWLIST)
    assert actions == {
        "research-engine-health": True,
        "task-transition": False,
        "routine-report-stage": False,
    }


def test_payload_has_required_fields_and_valid_hash(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    monkeypatch.setattr(
        vault_heartbeat, "last_dispatch_summary", lambda: {"status": "none"}
    )
    payload = vault_heartbeat.build_payload(vault)
    for field in REQUIRED_FIELDS:
        assert field in payload, field
    assert payload["schema_version"] == 1
    assert payload["report_kind"] == "odysseus-heartbeat"
    assert re.fullmatch(r"[0-9a-f]{64}", payload["actions_yaml_sha256"])
    assert payload["enabled_actions"] == ["research-engine-health"]
    assert isinstance(payload["last_dispatch"], dict)


def test_missing_allowlist_fails_closed(tmp_path):
    with pytest.raises(SystemExit):
        vault_heartbeat.build_payload(tmp_path)


def test_write_heartbeat_dated_filename_and_valid_json(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    monkeypatch.setattr(
        vault_heartbeat, "last_dispatch_summary", lambda: {"status": "none"}
    )
    payload = vault_heartbeat.build_payload(vault)
    target = vault_heartbeat.write_heartbeat(vault, payload)
    assert target.parent == vault / "automation" / "review" / "routine-reports" / "odysseus-heartbeat"
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}\.heartbeat\.json", target.name)
    loaded = json.loads(target.read_text(encoding="utf-8"))
    assert loaded == payload
    # No temp files left behind.
    assert list(target.parent.glob(".heartbeat-*.tmp")) == []


def test_last_dispatch_never_carries_result_or_prompt_fields(tmp_path, monkeypatch):
    vault = _make_vault(tmp_path)
    monkeypatch.setattr(
        vault_heartbeat,
        "last_dispatch_summary",
        lambda: {
            "status": "success",
            "task_name": "Research Engine Health",
            "started_at": "2026-06-12T05:17:00+00:00",
            "finished_at": "2026-06-12T05:18:00+00:00",
        },
    )
    payload = vault_heartbeat.build_payload(vault)
    forbidden = {"result", "error", "prompt", "steps", "tokens_used"}
    assert forbidden.isdisjoint(payload["last_dispatch"].keys())
