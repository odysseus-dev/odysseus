"""providerLabel() in providers.js must return port-specific names for local
servers, mirroring the Python _provider_label() in src/llm_core.py.

Without port discrimination every local endpoint shows as "Local", which means
a user with llama.cpp on :8080 and vLLM on :8000 can't tell them apart in the
UI. The rule: loopback + known port → named server; private-LAN IPs → "Local";
unknown loopback port → "Local".
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
    ("http://localhost:8080/v1",      "llama.cpp"),
    ("http://127.0.0.1:8080/v1",     "llama.cpp"),
    ("http://localhost:8000/v1",      "vLLM"),
    ("http://localhost:1234/v1",      "LM Studio"),
    ("http://localhost:11434/api",    "Ollama"),
    ("http://localhost:9999/v1",      "Local"),      # unknown port stays generic
    ("https://api.openai.com/v1",     "OpenAI"),
    ("https://api.groq.com/openai/v1","Groq"),
    ("http://192.168.1.50:8080",      "Local"),      # private LAN: no port branding
])
def test_provider_label_port_discrimination(url, expected):
    assert _provider_label(url) == expected
