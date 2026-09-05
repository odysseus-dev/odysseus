import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest


SCRIPT = Path(__file__).parents[1] / "integrations" / "codex" / "scripts" / "setup_mcp.py"
SPEC = importlib.util.spec_from_file_location("codex_setup_mcp", SCRIPT)
setup_mcp = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(setup_mcp)


class Runner:
    def __init__(self, current=None):
        self.current = current
        self.calls = []

    def __call__(self, args, **kwargs):
        self.calls.append((list(args), kwargs))
        if args[2:4] == ["get", "odysseus"]:
            if self.current is None:
                return SimpleNamespace(returncode=1, stdout="", stderr="not found")
            return SimpleNamespace(returncode=0, stdout=json.dumps(self.current), stderr="")
        return SimpleNamespace(returncode=0, stdout="", stderr="")


def _transport(url):
    return {
        "transport": {
            "type": "streamable_http",
            "url": url,
            "bearer_token_env_var": "ODYSSEUS_API_TOKEN",
        }
    }


def test_setup_adds_only_named_bridge():
    runner = Runner()
    assert setup_mcp.configure(base_url="https://ody.example/", runner=runner) == "configured"
    assert [call[0] for call in runner.calls] == [
        ["codex", "mcp", "get", "odysseus", "--json"],
        [
            "codex", "mcp", "add", "odysseus",
            "--url", "https://ody.example/api/codex/mcp/",
            "--bearer-token-env-var", "ODYSSEUS_API_TOKEN",
        ],
    ]
    assert all(call[1] == {"capture_output": True, "text": True, "check": False} for call in runner.calls)


def test_setup_is_idempotent_when_bridge_matches():
    url = "http://192.0.2.1:7000/api/codex/mcp/"
    runner = Runner(_transport(url))
    assert setup_mcp.configure(base_url="http://192.0.2.1:7000", runner=runner) == "already configured"
    assert len(runner.calls) == 1


def test_setup_replaces_only_stale_odysseus_entry():
    runner = Runner(_transport("https://old.example/api/codex/mcp/"))
    setup_mcp.configure(base_url="https://new.example", runner=runner)
    commands = [call[0] for call in runner.calls]
    assert commands[1] == ["codex", "mcp", "remove", "odysseus"]
    assert commands[2][0:4] == ["codex", "mcp", "add", "odysseus"]
    assert not any("node_repl" in command for command in commands)


@pytest.mark.parametrize("url", ["", "localhost:7000", "file:///tmp/odysseus"])
def test_setup_rejects_non_http_urls(url):
    with pytest.raises(ValueError):
        setup_mcp.configure(base_url=url, runner=Runner())
