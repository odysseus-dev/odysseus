from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COOKBOOK_RUNNING = ROOT / "static" / "js" / "cookbookRunning.js"


def _source() -> str:
    return COOKBOOK_RUNNING.read_text(encoding="utf-8")


def test_cookbook_marks_local_endpoint_registration_as_container_local():
    src = _source()
    assert "function _appendCookbookEndpointScope" in src
    assert "fd.append('container_local', 'true')" in src
    assert src.count("_appendCookbookEndpointScope(fd,") >= 3


def test_cookbook_does_not_use_local_as_endpoint_hostname():
    src = _source()
    assert "function _connectHostFromRemote" in src
    assert "if (!host || host === 'local') return fallback;" in src
    assert "const rawHost = task.remoteHost || 'localhost';" not in src


def test_cookbook_advertised_bind_urls_keep_connectable_host():
    src = _source()
    assert "function _endpointFromAdvertisedUrl" in src
    assert "_isAnyBindHost(u.hostname) ? currentHost" in src
    assert "host = u.hostname || host;" not in src
