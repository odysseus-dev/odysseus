from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_dockerfile_installs_agent_network_inspection_tools():
    dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")

    assert "iproute2" in dockerfile
    assert "iputils-ping" in dockerfile
    assert "net-tools" in dockerfile
    assert "dnsutils" in dockerfile
    assert "nmap" in dockerfile
