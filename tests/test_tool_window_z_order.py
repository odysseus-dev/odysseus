"""Regression coverage for shared tool-window z-order helpers."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HELPER = ROOT / "static" / "js" / "toolWindowZOrder.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _node_eval(source: str):
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=source,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


def test_top_tool_window_z_ignores_hidden_and_minimized_windows():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ topToolWindowZ }} from '{HELPER.as_uri()}';
            const cls = (...names) => ({{ contains: (name) => names.includes(name) }});
            const visible = {{ classList: cls(), style: {{ zIndex: '500' }} }};
            const hidden = {{ classList: cls('hidden'), style: {{ zIndex: '9999' }} }};
            const minimized = {{ classList: cls('modal-minimized'), style: {{ zIndex: '8888' }} }};
            const root = {{ querySelectorAll() {{ return [visible, hidden, minimized]; }} }};
            console.log(JSON.stringify({{ z: topToolWindowZ({{ root, getStyle: (el) => el.style, floor: 250 }}) }}));
            """
        )
    )

    assert values == {"z": 500}


def test_next_tool_window_z_preserves_already_frontmost_window():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ nextToolWindowZ }} from '{HELPER.as_uri()}';
            const cls = (...names) => ({{ contains: (name) => names.includes(name) }});
            const modal = {{ classList: cls(), style: {{ zIndex: '500' }} }};
            const root = {{ querySelectorAll() {{ return [modal]; }} }};
            console.log(JSON.stringify({{
              raised: nextToolWindowZ({{ root, getStyle: (el) => el.style, exclude: modal, current: '500', floor: 250 }}),
              newOne: nextToolWindowZ({{ root, getStyle: (el) => el.style, floor: 250 }}),
            }}));
            """
        )
    )

    assert values == {"raised": 500, "newOne": 501}


@pytest.mark.parametrize("rel", ["static/js/modalManager.js", "static/js/ui.js"])
def test_modal_surfaces_use_shared_tool_window_z_order(rel):
    src = (ROOT / rel).read_text(encoding="utf-8")
    assert "nextToolWindowZ" in src
    assert "./toolWindowZOrder.js" in src
