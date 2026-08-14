"""Printable composer text must win over global/custom keybindings."""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent
_MODULE = _REPO / "static" / "js" / "keyboard-shortcuts.js"
pytestmark = pytest.mark.skipif(
    shutil.which("node") is None, reason="node binary not on PATH"
)


def _matches(event: dict, combo: str, target: str = "TEXTAREA") -> bool:
    js = f"""
    import {{ _matchesCombo }} from '{_MODULE.as_uri()}';
    const ev = {json.dumps(event)};
    ev.target = {{ tagName: {json.dumps(target)}, type: 'text', isContentEditable: false }};
    ev.getModifierState = () => false;
    console.log(JSON.stringify(_matchesCombo(ev, {json.dumps(combo)}, false)));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js,
        capture_output=True,
        text=True,
        cwd=str(_REPO),
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_ampersand_operator_is_not_consumed_in_composer():
    event = {
        "key": "&",
        "ctrlKey": False,
        "metaKey": False,
        "altKey": False,
        "shiftKey": True,
    }
    assert _matches(event, "shift+&", "TEXTAREA") is False
    assert _matches(event, "shift+&", "DIV") is True


def test_altgr_fallback_protects_text_when_altgraph_is_missing():
    event = {
        "key": "@",
        "ctrlKey": True,
        "metaKey": False,
        "altKey": True,
        "shiftKey": False,
    }
    assert _matches(event, "ctrl+alt+@", "INPUT") is False


def test_command_modified_shortcut_remains_available_in_composer():
    event = {
        "key": "k",
        "ctrlKey": True,
        "metaKey": False,
        "altKey": False,
        "shiftKey": False,
    }
    assert _matches(event, "ctrl+k", "TEXTAREA") is True
