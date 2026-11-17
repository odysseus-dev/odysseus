"""A successful Tailscale query with no eligible hosts is still cached knowledge.

`discover_tailscale_hosts` gated its cache on the host list being non-empty, so a
valid "nothing to see here" answer looked identical to a cold cache and every
caller paid for another `tailscale status --json` (up to a 5s timeout). Failures
stay uncached so a peer coming online is still picked up promptly.
"""

import pytest

from src import model_discovery


class _Result:
    def __init__(self, returncode, stdout):
        self.returncode = returncode
        self.stdout = stdout


@pytest.fixture
def tailscale(monkeypatch):
    """Count `tailscale status` invocations and start from a cold cache."""
    calls = []

    def _record(result):
        def _run(*_args, **_kwargs):
            calls.append(1)
            if isinstance(result, Exception):
                raise result
            return result
        monkeypatch.setattr(model_discovery.subprocess, "run", _run)
        return calls

    monkeypatch.setattr(model_discovery, "_hosts_cache", [])
    monkeypatch.setattr(model_discovery, "_hosts_cache_time", 0)
    return _record


def test_empty_but_successful_discovery_is_only_run_once(tailscale):
    calls = tailscale(_Result(0, '{"Self":{},"Peer":{}}'))

    assert model_discovery.discover_tailscale_hosts() == []
    assert model_discovery.discover_tailscale_hosts() == []
    assert len(calls) == 1


def test_nonempty_discovery_is_still_cached(tailscale):
    calls = tailscale(_Result(0, '{"Self":{"TailscaleIPs":["100.1.1.1"]},"Peer":{}}'))

    assert model_discovery.discover_tailscale_hosts() == ["100.1.1.1"]
    assert model_discovery.discover_tailscale_hosts() == ["100.1.1.1"]
    assert len(calls) == 1


@pytest.mark.parametrize(
    "result",
    [
        _Result(1, ""),                       # tailscale installed but logged out
        _Result(0, "not json"),               # unparseable output
        FileNotFoundError("tailscale"),       # not installed
    ],
    ids=["nonzero_exit", "bad_json", "not_installed"],
)
def test_failures_stay_retryable(tailscale, result):
    calls = tailscale(result)

    assert model_discovery.discover_tailscale_hosts() == []
    assert model_discovery.discover_tailscale_hosts() == []
    assert len(calls) == 2
