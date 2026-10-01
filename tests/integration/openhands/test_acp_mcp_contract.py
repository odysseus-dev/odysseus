from pathlib import Path
import importlib.util
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "openhands_probe", ROOT / "scripts/openhands_probe.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe():
    return _load_probe()


@pytest.fixture
def stack(probe):
    return probe.acp_mcp_stack()


_DIRECT_ACP = pytest.mark.xfail(
    reason=(
        "OpenCode and Hermes ACP binaries are absent; Agent Server wrappers "
        "are claude-agent-acp/codex-acp/gemini. Direct ACP MCP forwarding "
        "cannot be proven on the pinned stack."
    ),
    strict=True,
)


@_DIRECT_ACP
@pytest.mark.parametrize("profile", ["opencode", "hermes"])
def test_acp_receives_scoped_mcp_without_secret_leak(stack, profile):
    run = stack.start_acp_probe(profile, scopes={"notes.read"})
    assert stack.mcp_call(run, "notes.read").allowed
    assert not stack.mcp_call(run, "mail.send").allowed
    assert not stack.find_secret(run.token_id, sources=("logs", "events", "profile", "workspace"))


@_DIRECT_ACP
@pytest.mark.parametrize("profile", ["opencode", "hermes"])
def test_acp_reconnect_keeps_broker_context(stack, profile):
    run = stack.start_acp_probe(profile, scopes={"notes.read"})
    stack.disconnect(run)
    resumed = stack.reconnect(run)
    assert resumed.profile == profile
    assert stack.mcp_call(resumed, "notes.read").allowed


@_DIRECT_ACP
@pytest.mark.parametrize("profile", ["opencode", "hermes"])
def test_acp_cancel_stops_forwarding(stack, profile):
    run = stack.start_acp_probe(profile, scopes={"notes.read"})
    cancelled = stack.cancel(run)
    assert cancelled.status in {"cancelled", "canceled"}
    assert not stack.mcp_call(run, "notes.read").allowed


def test_probe_records_per_profile_capability_matrix(probe):
    result = probe.probe_acp_mcp()
    assert result.name == "acp-mcp"
    matrix = result.evidence["profiles"]
    for profile in ("opencode", "hermes"):
        record = matrix[profile]
        assert "forwarding" in record
        assert "reconnect" in record
        assert "resume" in record
        assert "cancellation" in record
        assert "secret_handling" in record
    assert result.evidence["selected_branch"] in {"direct", "proxy"}
    if result.evidence["selected_branch"] != "direct":
        assert result.passed is False
        assert result.evidence["proxy_required"] is True


def test_probe_cli_accepts_acp_mcp(probe, monkeypatch):
    monkeypatch.setattr(sys, "argv", ["openhands_probe.py", "acp-mcp", "--json"])
    classified = probe.ProbeResult(
        name="acp-mcp",
        passed=False,
        evidence={"selected_branch": "proxy"},
    )
    monkeypatch.setattr(probe, "probe_acp_mcp", lambda: classified)
    assert probe.main() in {0, 1}
