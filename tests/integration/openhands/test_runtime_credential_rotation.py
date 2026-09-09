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
    return probe.credential_stack()


_DIRECT_ROTATION = pytest.mark.xfail(
    reason=(
        "Agent Server 1.45.0 conversation identity is stable across "
        "POST /secrets, but T2 accept / T1 401 were unobserved: no "
        "token-validating MCP, OpenCode/Hermes binaries absent, and ACP "
        "secret updates restart the session on the next turn"
    ),
    strict=True,
)


@_DIRECT_ROTATION
@pytest.mark.parametrize("profile", ["openhands", "opencode", "hermes"])
def test_live_runtime_rotates_without_restart(stack, profile):
    run = stack.start_token_probe(profile, token="T1")
    before = stack.runtime_identity(run)
    stack.rotate_probe_token(run, old="T1", new="T2")
    assert stack.call_mcp(run, "T2").status == 200
    assert stack.call_mcp(run, "T1").status == 401
    assert stack.runtime_identity(run) == before


def test_credential_delivery_mode_enum_values(probe):
    mode = probe.CredentialDeliveryMode
    assert mode.DIRECT_ROTATION == "direct_rotation"
    assert mode.BROKER == "broker"
    assert {item.value for item in mode} == {"direct_rotation", "broker"}


def test_probe_classifies_explicit_delivery_mode(probe):
    result = probe.probe_credential_rotation()
    assert result.name == "credential-rotation"
    mode = result.evidence["mode"]
    assert mode in {item.value for item in probe.CredentialDeliveryMode}
    assert result.evidence["selected_branch"] == mode
    identities = result.evidence["runtime_identities"]
    assert isinstance(identities, dict)
    for profile in ("openhands", "opencode", "hermes"):
        assert profile in identities
    assert "old_token_rejection" in result.evidence
    assert "redaction_checks" in result.evidence
    if mode == probe.CredentialDeliveryMode.DIRECT_ROTATION:
        assert result.passed is True
        for profile in ("openhands", "opencode", "hermes"):
            identity = identities[profile]
            assert identity.get("observed") is True
            assert identity.get("before")
            assert identity["before"] == identity.get("after")
        assert result.evidence["old_token_rejection"]["T1"] == 401
    else:
        assert result.passed is False
        assert mode == probe.CredentialDeliveryMode.BROKER


def test_probe_cli_accepts_credential_rotation(probe, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["openhands_probe.py", "credential-rotation", "--json"],
    )
    classified = probe.ProbeResult(
        name="credential-rotation",
        passed=False,
        evidence={"mode": "broker"},
    )
    monkeypatch.setattr(probe, "probe_credential_rotation", lambda: classified)
    assert probe.main() in {0, 1}
