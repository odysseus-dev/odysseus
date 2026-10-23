"""Discovery dedupe must not merge distinct machines (#6221).

`discover_models()` deduplicated scan results on (port, model_ids) only, so two
different hosts serving the same models on the same port collapsed into one
result, and which one survived depended on `as_completed()` ordering. Only this
machine's own aliases (loopback, host.docker.internal, its Tailscale IP) are
the same endpoint seen twice.
"""

from src import model_discovery
from src.model_discovery import ModelDiscovery


def _discover(monkeypatch, inventory, self_tailscale_ip=None):
    def fake_check(self, host, port):
        models = inventory.get((host, port))
        if not models:
            return None
        return {
            "host": host,
            "port": port,
            "url": f"http://{host}:{port}/v1/chat/completions",
            "models": models,
            "models_display": models,
            "provider": None,
        }

    hosts = list(dict.fromkeys(h for h, _ in inventory))
    monkeypatch.setattr(ModelDiscovery, "_check_port", fake_check)
    monkeypatch.setattr(ModelDiscovery, "_get_hosts", lambda self: hosts)
    monkeypatch.setattr(model_discovery, "_self_tailscale_ip", self_tailscale_ip)
    discovery = ModelDiscovery("localhost")
    discovery._extra_ports = {8000}
    return sorted((i["host"], i["port"]) for i in discovery.discover_models()["items"])


def test_distinct_hosts_with_identical_models_are_all_kept(monkeypatch):
    found = _discover(monkeypatch, {
        ("10.0.0.10", 11434): ["llama3.2"],
        ("10.0.0.11", 11434): ["llama3.2"],
        ("10.0.0.12", 8000): ["llama3.2"],
        ("10.0.0.13", 11434): ["llama3.2", "qwen3"],
    })

    assert found == [
        ("10.0.0.10", 11434),
        ("10.0.0.11", 11434),
        ("10.0.0.12", 8000),
        ("10.0.0.13", 11434),
    ]


def test_this_machine_reached_through_several_aliases_is_listed_once(monkeypatch):
    found = _discover(
        monkeypatch,
        {
            ("localhost", 11434): ["llama3.2"],
            ("host.docker.internal", 11434): ["llama3.2"],
            ("100.64.0.1", 11434): ["llama3.2"],  # this machine's Tailscale IP
            ("100.64.0.2", 11434): ["llama3.2"],  # a Tailscale peer
        },
        self_tailscale_ip="100.64.0.1",
    )

    assert len(found) == 2
    assert ("100.64.0.2", 11434) in found


def test_dedupe_host_key_groups_only_local_aliases(monkeypatch):
    monkeypatch.setattr(model_discovery, "_self_tailscale_ip", "100.64.0.1")
    key = model_discovery._dedupe_host_key

    for host in ("localhost", "127.0.0.1", "127.0.1.1", "::1", "host.docker.internal", "100.64.0.1"):
        assert key(host) == "local", host
    assert key("10.0.0.10") == "10.0.0.10"
    assert key("100.64.0.2") == "100.64.0.2"
