"""Pin the Mistral provider-logo pattern to cover Mixtral and Ministral.

The pattern was /mistral/i, which does not match "mixtral" (note the x) or
"ministral" -- Mistral AI's flagship MoE and edge families -- so those models
rendered with no provider logo unless they carried a "mistralai/" prefix.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "providers.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


def _has_logo(model):
    js = (
        f"import {{ providerLogo }} from '{_HELPER.as_uri()}';"
        f"console.log(JSON.stringify(providerLogo({json.dumps(model)}) !== null));"
    )
    p = subprocess.run(["node", "--input-type=module"], input=js, capture_output=True, text=True, encoding="utf-8", cwd=str(_REPO), timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip())


def test_mixtral_ministral_get_a_logo():
    assert _has_logo("mixtral-8x7b") is True
    assert _has_logo("ministral-8b") is True
    assert _has_logo("mistral-large-latest") is True


def test_unknown_vendor_has_no_logo():
    assert _has_logo("totally-unknown-model-xyz") is False
