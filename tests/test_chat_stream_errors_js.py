"""Execute terminal stream-error classification under Node."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parents[1]
_MODULE = (_REPO / "static" / "js" / "chatStreamErrors.js").as_uri()


def test_terminal_provider_errors_preserve_text_and_never_auto_retry():
    if not shutil.which("node"):
        pytest.skip("node is not installed")

    script = f"""
      import {{ createTerminalStreamError, isRecoverableStreamError }} from {json.dumps(_MODULE)};
      const stringError = createTerminalStreamError({{ status: 401, error: 'invalid key' }});
      const objectError = createTerminalStreamError({{ status: 404, error: {{ message: 'model missing' }} }});
      console.log(JSON.stringify({{
        stringMessage: stringError.message,
        objectMessage: objectError.message,
        terminalRecoverable: isRecoverableStreamError(stringError),
        eofRecoverable: isRecoverableStreamError(new Error('Stream closed before completion')),
        networkRecoverable: isRecoverableStreamError(new TypeError('fetch failed')),
      }}));
    """
    result = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        cwd=_REPO,
        timeout=30,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == {
        "stringMessage": "invalid key",
        "objectMessage": "model missing",
        "terminalRecoverable": False,
        "eofRecoverable": True,
        "networkRecoverable": True,
    }
