"""Pin the billing/display classifier `isLocalEndpoint` in chatRenderer.js.

Self-hosted endpoints reached by a bare Docker/Compose service name (e.g.
`http://llamaswap:8000`) must classify as LOCAL so they aren't priced at cloud
rates against the substring-matched MODEL_PRICING table. Cloud FQDNs must stay
billable.

Driven through `node --input-type=module` against the real function (extracted
from source — chatRenderer.js can't be imported standalone since it pulls in
browser-only modules), same spirit as test_reply_recipients_js.py. Skips when
`node` is not installed rather than failing.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "static" / "js" / "chatRenderer.js"
_HAS_NODE = shutil.which("node") is not None


def _is_local(url: str) -> bool:
    src = _SRC.read_text(encoding="utf-8")
    m = re.search(r"export function isLocalEndpoint\(.*?\n\}", src, re.DOTALL)
    assert m, "isLocalEndpoint not found in chatRenderer.js"
    fn = m.group(0).replace("export function", "function", 1)
    js = fn + f"\nconsole.log(JSON.stringify(isLocalEndpoint({json.dumps(url)})));"
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("url", [
    "http://llamaswap:8000",            # bare Docker/Compose service name
    "http://nim-nano:8000/v1",
    "http://localhost:7000",
    "http://127.0.0.1:11434",
    "http://192.168.50.244",            # private ranges
    "http://10.0.0.5:8080",
    "http://172.16.0.9",
    "http://server.local",              # mDNS / .local
])
def test_self_hosted_endpoints_classify_local(url):
    assert _is_local(url) is True, f"{url} should be treated as local (free)"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("url", [
    "https://api.openai.com/v1",
    "https://openrouter.ai/api/v1",
    "https://api.anthropic.com",
    "https://generativelanguage.googleapis.com",
])
def test_cloud_endpoints_classify_billable(url):
    assert _is_local(url) is False, f"{url} should NOT be treated as local"
