"""Quarantine the 9router DATA_DIR sqlite mount as a temporary bridge."""

from __future__ import annotations

from pathlib import Path

import pytest

from services.ninerouter.metadata import NineRouterMetadataClient, NineRouterMetadataError


ROOT = Path(__file__).resolve().parents[3]
OVERLAY = ROOT / "docker-compose.openhands.yml"
EVIDENCE = ROOT / "docs/architecture/evidence/9router-datadir-bridge.md"
BOOTSTRAP = ROOT / "deploy/openhands/bootstrap_native_llm.py"
WORKER = ROOT / "services/agents/model_job_worker.py"
BRIDGE_MARK = "TEMPORARY BRIDGE"
DELETION_MARK = "Deletion condition"
PREFERRED = "Agent Server → supported 9router control/API"
OFFICIAL_KEY_API = "POST /api/keys"


def _service_block(compose: str, name: str) -> str:
    header = f"  {name}:"
    lines = compose.splitlines()
    start = next((index for index, line in enumerate(lines) if line == header), None)
    if start is None:
        return ""
    end = len(lines)
    for index in range(start + 1, len(lines)):
        line = lines[index]
        if line.startswith("  ") and not line.startswith("    ") and line.endswith(":"):
            end = index
            break
        if line and not line.startswith(" ") and not line.startswith("#"):
            end = index
            break
    return "\n".join(lines[start:end])


def test_datadir_bridge_is_documented_temporary_with_deletion_condition():
    evidence = EVIDENCE.read_text(encoding="utf-8")
    assert BRIDGE_MARK in evidence
    assert DELETION_MARK in evidence
    assert PREFERRED in evidence
    assert OFFICIAL_KEY_API in evidence
    assert "provisional" in evidence.lower()
    assert "not settled" in evidence.lower()


def test_overlay_datadir_mounts_are_quarantined_not_owned_by_odysseus():
    compose = OVERLAY.read_text(encoding="utf-8")
    odysseus = _service_block(compose, "odysseus")
    agent = _service_block(compose, "openhands-agent-server")
    worker = _service_block(compose, "odysseus-model-jobs")
    router = _service_block(compose, "9router")
    assert odysseus, "overlay must keep odysseus service"
    assert agent, "overlay must keep Agent Server"
    assert worker, "overlay must keep model-job worker"
    assert router, "overlay must keep 9router"
    assert "9router:/opt/odysseus/9router-data" in agent
    assert "9router:/opt/odysseus/9router-data" in worker
    assert "9router:/app/data" in router
    assert "9router:" not in odysseus
    assert "NINE_ROUTER_SQLITE" not in odysseus
    for block in (agent, worker):
        assert BRIDGE_MARK in block
        assert DELETION_MARK in block
        assert OFFICIAL_KEY_API in block
        assert PREFERRED in block


def test_sqlite_minters_declare_temporary_bridge():
    bootstrap = BOOTSTRAP.read_text(encoding="utf-8")
    worker = WORKER.read_text(encoding="utf-8")
    assert BRIDGE_MARK in bootstrap
    assert BRIDGE_MARK in worker
    assert OFFICIAL_KEY_API in bootstrap
    assert OFFICIAL_KEY_API in worker
    assert DELETION_MARK in bootstrap
    assert DELETION_MARK in worker


def test_odysseus_metadata_client_cannot_mint_9router_keys():
    seen: list[str] = []

    def fetch(path: str, headers: dict[str, str]) -> dict[str, object]:
        seen.append(path)
        return {"ok": True}

    client = NineRouterMetadataClient(base_url="http://9router:20128", fetch=fetch)
    with pytest.raises(NineRouterMetadataError, match="allowlist"):
        client.get("/api/keys")
    assert not hasattr(client, "post")
    assert not hasattr(client, "request")
    assert seen == []
