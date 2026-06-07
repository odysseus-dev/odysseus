"""Pin the Perplexity provider logo + endpoint label.

The Perplexity connector relies on the pre-existing `/perplexity|sonar/i` logo
pattern and the `perplexity.ai` → "Perplexity" endpoint label. These guard
against either being dropped, which would leave the new provider unbranded.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_HELPER = _REPO / "static" / "js" / "providers.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node not on PATH")


def _eval(expr_js):
    js = (
        f"import {{ providerLogo, providerLabel }} from '{_HELPER.as_posix()}';"
        f"console.log(JSON.stringify({expr_js}));"
    )
    p = subprocess.run(["node", "--input-type=module"], input=js,
                       capture_output=True, text=True, cwd=str(_REPO), timeout=30)
    assert p.returncode == 0, p.stderr
    return json.loads(p.stdout.strip())


def test_perplexity_models_get_a_logo():
    assert _eval("providerLogo('perplexity/sonar') !== null") is True
    assert _eval("providerLogo('sonar-pro') !== null") is True


def test_perplexity_endpoint_label():
    assert _eval("providerLabel('https://api.perplexity.ai/v1')") == "Perplexity"
