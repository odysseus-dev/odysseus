import json
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_eval(source: str):
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


def test_extract_quote_meta_ignores_non_string_inputs():
    values = _node_eval(
        """
        globalThis.document = {
          createElement() {
            return {
              set textContent(value) { this._text = value; },
              get innerHTML() { return this._text || ''; }
            };
          }
        };
        const { _extractQuoteMeta } = await import('./static/js/emailLibrary/signatureFold.js');
        console.log(JSON.stringify({
          nullValue: _extractQuoteMeta(null),
          objectValue: _extractQuoteMeta({bad: true})
        }));
        """
    )

    assert values == {"nullValue": "", "objectValue": ""}


def test_extract_quote_meta_keeps_outlook_headers():
    values = _node_eval(
        """
        globalThis.document = {
          createElement() {
            return {
              set textContent(value) { this._text = value; },
              get innerHTML() { return this._text || ''; }
            };
          }
        };
        const { _extractQuoteMeta } = await import('./static/js/emailLibrary/signatureFold.js');
        const html = 'From: Alice <alice@example.com> Sent: Monday, May 4, 2026 To: Bob Subject: hi';
        console.log(JSON.stringify({ meta: _extractQuoteMeta(html) }));
        """
    )

    assert values["meta"] == "Alice · Monday, May 4, 2026"
