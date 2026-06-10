"""Pin the Settings Local/API grouping classifier `_isLocalEndpoint` in admin.js.

The Add-Models panel splits endpoints into a "Local" and an "API / Cloud"
section using `_isLocalEndpoint(base_url)`. A host Ollama discovered by "Scan
for Servers" is reached via the Docker host-gateway alias
`host.docker.internal:11434`; it must classify as LOCAL so it shows under
Local rather than being filed with cloud providers (issue #1292 — "the UI
displayed incorrectly ... doesn't seem right"). Mirrors the billing classifier
chatRenderer.js:isLocalEndpoint, which already treats host.docker.internal as
local.

Driven through `node` against the real function extracted from source (admin.js
can't be imported standalone — it pulls in browser-only modules), same spirit
as test_local_endpoint_js.py. Skips when `node` is not installed.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "static" / "js" / "admin.js"
_HAS_NODE = shutil.which("node") is not None


def _is_local(url: str) -> bool:
    src = _SRC.read_text(encoding="utf-8")
    m = re.search(r"function _isLocalEndpoint\(.*?\n\}", src, re.DOTALL)
    assert m, "_isLocalEndpoint not found in admin.js"
    js = m.group(0) + f"\nconsole.log(JSON.stringify(_isLocalEndpoint({json.dumps(url)})));"
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("url", [
    "http://host.docker.internal:11434/v1",  # host Ollama via Docker gateway (#1292)
    "http://192.168.1.50:11434/v1",          # same host Ollama via LAN IP
    "http://localhost:11434/v1",
    "http://127.0.0.1:11434",
    "http://10.0.0.5:8080",
    "http://172.16.0.9",
    "http://llamaswap:8000",                 # bare Docker/Compose service name
    "http://server.local",
])
def test_self_hosted_endpoints_group_local(url):
    assert _is_local(url) is True, f"{url} should group under Local"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1",
    "https://openrouter.ai/api/v1",
    "https://api.anthropic.com",
])
def test_cloud_endpoints_group_api(url):
    assert _is_local(url) is False, f"{url} should group under API / Cloud"
