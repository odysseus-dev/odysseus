"""Pin email recipient display formatting around quoted commas."""

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


def test_format_recipients_keeps_quoted_comma_display_name_together():
    values = _node_eval(
        """
        const { _formatRecipients } = await import('./static/js/emailLibrary/utils.js');
        console.log(JSON.stringify({
          one: _formatRecipients('"Doe, Jane" <jane@x.com>'),
          two: _formatRecipients('"Doe, Jane" <jane@x.com>, Bob <bob@y.com>'),
          three: _formatRecipients('"Doe, Jane" <jane@x.com>, Bob <bob@y.com>, carol@z.com')
        }));
        """
    )

    assert values == {
        "one": "Doe, Jane",
        "two": "Doe, Jane, Bob",
        "three": "Doe, Jane, Bob +1",
    }
