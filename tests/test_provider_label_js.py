"""providerLabel() in providers.js must NOT name the serving tool from the port,
mirroring the Python _provider_label() in src/llm_core.py.

A port is not authoritative: vLLM, SGLang, llama.cpp and plain OpenAI-compatible
servers all routinely share 8000/8080, so a port-only label would mislabel real
setups (e.g. a vLLM box on :8080 shown as "llama.cpp"). The actual tool is
identified by probing /props during discovery and stored as the endpoint's name.
The rule here: loopback → "Local"; private-LAN IPs → "Local"; known remote
provider hosts → their provider name.
"""
import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_SRC = _REPO / "static" / "js" / "providers.js"
_HAS_NODE = shutil.which("node") is not None


def _provider_label(url: str) -> str | None:
    src = _SRC.read_text(encoding="utf-8")
    # Strip the `export` keyword so the module runs standalone.
    src_runnable = src.replace("export function providerLabel", "function providerLabel")
    src_runnable = src_runnable.replace("export default {", "const _default = {")
    js = src_runnable + f"\nconsole.log(JSON.stringify(providerLabel({json.dumps(url)})));"
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, encoding="utf-8",
        cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
@pytest.mark.parametrize("url,expected", [
    # Loopback never names the tool from the port — it isn't authoritative.
    ("http://localhost:8080/v1",      "Local"),
    ("http://127.0.0.1:8080/v1",      "Local"),
    ("http://localhost:8000/v1",      "Local"),
    ("http://localhost:1234/v1",      "Local"),
    ("http://localhost:11434/api",    "Local"),
    ("http://localhost:9999/v1",      "Local"),
    # Known remote provider hosts are still labeled by host suffix.
    ("https://api.openai.com/v1",     "OpenAI"),
    ("https://api.groq.com/openai/v1","Groq"),
    ("http://192.168.1.50:8080",      "Local"),      # private LAN: no port branding
])
def test_provider_label_neutral_for_loopback(url, expected):
    assert _provider_label(url) == expected
