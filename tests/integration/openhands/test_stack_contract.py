from pathlib import Path
import importlib.util
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture
def version_file():
    def read(relative_path: str) -> dict[str, str]:
        path = ROOT / relative_path
        values: dict[str, str] = {}
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            key, value = line.split("=", 1)
            values[key] = value
        return values

    return read


def test_all_first_party_components_are_pinned(version_file):
    values = version_file("deploy/openhands/versions.env")

    assert values.keys() >= {
        "OPENHANDS_AGENT_SERVER_IMAGE",
        "OPENHANDS_AUTOMATION_IMAGE",
        "OPENHANDS_CANVAS_IMAGE",
        "NINE_ROUTER_IMAGE",
        "OPENCODE_VERSION",
        "HERMES_VERSION",
    }
    assert all(value and ":latest" not in value for value in values.values())
    assert "@sha256:" in values["NINE_ROUTER_IMAGE"]
    compose = (ROOT / "docker-compose.openhands.yml").read_text(encoding="utf-8")
    for key in (
        "OPENHANDS_AGENT_SERVER_IMAGE",
        "OPENHANDS_AUTOMATION_IMAGE",
        "OPENHANDS_CANVAS_IMAGE",
        "NINE_ROUTER_IMAGE",
    ):
        assert values[key] in compose


def test_probe_accepts_compose_json_lines(monkeypatch):
    spec = importlib.util.spec_from_file_location("openhands_probe", ROOT / "scripts/openhands_probe.py")
    assert spec and spec.loader
    probe = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, probe)
    spec.loader.exec_module(probe)

    responses = iter((
        '{"services": {"openhands-agent-server": {}, "openhands-automation": {}, "openhands-canvas": {}, "odysseus-mcp": {}}}',
        '{"Service": "openhands-agent-server", "State": "running", "Health": "healthy"}\n',
        '',
    ))
    monkeypatch.setattr(probe, "_compose", lambda *args: type("Result", (), {"returncode": 0, "stdout": next(responses), "stderr": ""})())

    assert {result.name for result in probe.probe_stack()} == set(probe.SERVICES)
