"""The normal chat send path must safely query the integrated document facade."""

import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]


@pytest.mark.skipif(not shutil.which("node"), reason="Node.js is required for the Chromium regression test")
def test_normal_chat_send_with_real_document_module():
    result = subprocess.run(
        ["node", str(ROOT / "tests" / "documentChatSendRemediation.mjs")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stderr
    assert "PASS actual chat submission" in result.stdout
