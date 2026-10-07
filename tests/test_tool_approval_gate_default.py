"""Pin the production default of the post-external-context approval gate.

Every other gate test sets ``TOOL_APPROVAL_GATE_ENABLED`` explicitly, so none of
them notices if the default flips. The flag is read once at import, so each
case imports the module in a fresh interpreter with a controlled environment.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[1]

_PROBE = """
import json
from src.tool_capabilities import TOOL_APPROVAL_GATE_ENABLED, ToolRunSecurityContext
context = ToolRunSecurityContext(external_untrusted_context_seen=True, external_sources=["read_email"])
print(json.dumps({
    "enabled": TOOL_APPROVAL_GATE_ENABLED,
    "allowed": {tool: context.decision_for(tool, "{}").allowed
                for tool in ("bash", "send_email", "delete_email", "read_file")},
}))
"""


def _probe(gate_value):
    env = {key: value for key, value in os.environ.items() if key != "ODYSSEUS_TOOL_APPROVAL_GATE"}
    if gate_value is not None:
        env["ODYSSEUS_TOOL_APPROVAL_GATE"] = gate_value
    out = subprocess.run(
        [sys.executable, "-c", _PROBE], cwd=REPO, env=env,
        capture_output=True, text=True, timeout=60, check=True,
    )
    return json.loads(out.stdout.strip().splitlines()[-1])


def test_gate_is_on_when_the_variable_is_unset():
    result = _probe(None)
    assert result["enabled"] is True
    assert result["allowed"] == {
        "bash": False, "send_email": False, "delete_email": False, "read_file": True,
    }


@pytest.mark.parametrize("value", ["0", "false", "no", "off", " OFF "])
def test_gate_can_be_turned_off_explicitly(value):
    result = _probe(value)
    assert result["enabled"] is False
    assert all(result["allowed"].values())
