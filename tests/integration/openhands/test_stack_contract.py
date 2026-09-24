from pathlib import Path
import importlib.util
import re
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


def test_probe_compose_files_exclude_hhpe_relay(monkeypatch):
    """Overlay startup is the app, OpenHands, and observability compose files.

    The probe must never include an HHPE relay. Observability is unpublished:
    guest loopback ports only, no Jaeger, no OpenLIT.
    """

    spec = importlib.util.spec_from_file_location(
        "openhands_probe", ROOT / "scripts/openhands_probe.py"
    )
    assert spec and spec.loader
    probe = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, probe)
    spec.loader.exec_module(probe)

    assert probe.COMPOSE_FILES == (
        "docker-compose.yml",
        "docker-compose.openhands.yml",
        "docker-compose.observability.yml",
    )
    overlay = (ROOT / "docker-compose.openhands.yml").read_text(encoding="utf-8")
    assert "docker-compose.relay.yml" not in overlay
    assert "hhpe-adapter" not in overlay

    obs_path = ROOT / "docker-compose.observability.yml"
    obs = obs_path.read_text(encoding="utf-8")
    obs_lower = obs.lower()
    for name in (
        "otel-collector",
        "tempo",
        "prometheus",
        "grafana",
        "langfuse-web",
        "langfuse-worker",
        "langfuse-postgres",
        "langfuse-redis",
        "langfuse-minio",
        "langfuse-clickhouse",
    ):
        assert name in obs
    assert "langfuse" in obs_lower
    assert "127.0.0.1" in obs
    assert "jaeger" not in obs_lower
    assert "openlit" not in obs_lower
    assert "docker-compose.relay.yml" not in obs
    assert "hhpe-adapter" not in obs_lower
    # Collector is overlay DNS only. These ports must not be host-published.
    assert "4317:" not in obs
    assert "4318:" not in obs
    assert "8889:" not in obs
    for mapping in (
        "127.0.0.1:3000:3000",
        "127.0.0.1:3001:3000",
        "127.0.0.1:3200:3200",
        "127.0.0.1:9090:9090",
    ):
        assert mapping in obs
    published = re.findall(
        r"""(?m)^\s+-\s+["']?((?:\d+\.\d+\.\d+\.\d+:)?\d+:\d+)["']?\s*$""",
        obs,
    )
    assert published, "observability compose publishes no host ports"
    assert all(item.startswith("127.0.0.1:") for item in published), published

    versions = (ROOT / "deploy/observability/versions.env").read_text(encoding="utf-8")
    assert ":latest" not in versions
    for pin in (
        "OTEL_COLLECTOR_IMAGE=otel/opentelemetry-collector-contrib:0.122.1",
        "TEMPO_IMAGE=grafana/tempo:2.7.2",
        "PROMETHEUS_IMAGE=prom/prometheus:v3.2.1",
        "GRAFANA_IMAGE=grafana/grafana:11.6.0",
        "LANGFUSE_IMAGE=langfuse/langfuse:3.29.0",
    ):
        assert pin in versions

    collector = (ROOT / "deploy/observability/otel-collector.yaml").read_text(encoding="utf-8")
    collector_lower = collector.lower()
    for key in (
        "authorization",
        "api_key",
        "cookie",
        "x-9r-cli-token",
        "virtual_key",
        "gen_ai.input.messages",
        "gen_ai.output.messages",
    ):
        assert key in collector
    assert "tempo:4317" in collector
    assert "http://langfuse-web:3000/api/public/otel" in collector
    assert "0.0.0.0:8889" in collector
    assert "jaeger" not in collector_lower
    assert "LANGFUSE_OTLP_AUTH" in collector
