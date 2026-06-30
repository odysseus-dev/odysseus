"""Node-driven regression coverage for Notes pane z-order selection.

Notes uses a body-level backdrop instead of the shared `.modal` element, so the
shared tool-window stack helper must account for both Notes and normal modals
without importing the full browser-heavy modules.
"""

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


def test_notes_z_order_uses_floor_when_no_tool_windows_are_open():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ topToolWindowZ }} from '{HELPER.as_uri()}';
            const root = {{ querySelectorAll() {{ return []; }} }};
            console.log(JSON.stringify({{ z: topToolWindowZ({{ root, getStyle: () => ({{}}) }}) }}));
            """
        )
    )

    assert values == {"z": 250}


def test_notes_z_order_lands_above_highest_visible_tool_window():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ topToolWindowZ }} from '{HELPER.as_uri()}';
            const cls = (...names) => ({{ contains: (name) => names.includes(name) }});
            const elements = [
              {{ id: 'memory', classList: cls(), style: {{ zIndex: '320' }} }},
              {{ id: 'research', classList: cls(), style: {{ zIndex: '415' }} }},
              {{ id: 'invalid', classList: cls(), style: {{ zIndex: 'auto' }} }},
            ];
            const root = {{ querySelectorAll() {{ return elements; }} }};
            const top = topToolWindowZ({{ root, getStyle: (el) => el.style }});
            console.log(JSON.stringify({{ top, notes: top + 1 }}));
            """
        )
    )

    assert values == {"top": 415, "notes": 416}


def test_modal_z_order_handoff_lands_above_notes_tie_on_first_click():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ nextToolWindowZ }} from '{HELPER.as_uri()}';
            const cls = (...names) => ({{ contains: (name) => names.includes(name) }});
            const modal = {{ id: 'modal', classList: cls(), style: {{ zIndex: '416' }} }};
            const notes = {{ id: 'notes', classList: cls(), style: {{ zIndex: '416' }} }};
            const elements = [modal, notes];
            const root = {{ querySelectorAll() {{ return elements; }} }};
            const z = nextToolWindowZ({{
              exclude: modal,
              current: modal.style.zIndex,
              root,
              getStyle: (el) => el.style,
            }});
            console.log(JSON.stringify({{ z }}));
            """
        )
    )

    assert values == {"z": 417}


def test_modal_z_order_keeps_current_z_when_already_above_stack():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ nextToolWindowZ }} from '{HELPER.as_uri()}';
            const cls = (...names) => ({{ contains: (name) => names.includes(name) }});
            const modal = {{ id: 'modal', classList: cls(), style: {{ zIndex: '420' }} }};
            const notes = {{ id: 'notes', classList: cls(), style: {{ zIndex: '416' }} }};
            const root = {{ querySelectorAll() {{ return [modal, notes]; }} }};
            const z = nextToolWindowZ({{
              exclude: modal,
              current: modal.style.zIndex,
              root,
              getStyle: (el) => el.style,
            }});
            console.log(JSON.stringify({{ z }}));
            """
        )
    )

    assert values == {"z": 420}


def test_notes_z_order_ignores_hidden_minimized_and_excluded_windows():
    values = _node_eval(
        textwrap.dedent(
            f"""
            import {{ topToolWindowZ }} from '{HELPER.as_uri()}';
            const cls = (...names) => ({{ contains: (name) => names.includes(name) }});
            const excluded = {{ id: 'notes', classList: cls(), style: {{ zIndex: '900' }} }};
            const elements = [
              excluded,
              {{ id: 'hidden-class', classList: cls('hidden'), style: {{ zIndex: '800' }} }},
              {{ id: 'minimized', classList: cls('modal-minimized'), style: {{ zIndex: '700' }} }},
              {{ id: 'display-none', classList: cls(), style: {{ zIndex: '600', display: 'none' }} }},
              {{ id: 'visibility-hidden', classList: cls(), style: {{ zIndex: '500', visibility: 'hidden' }} }},
              {{ id: 'visible', classList: cls(), style: {{ zIndex: '310' }} }},
            ];
            const root = {{ querySelectorAll() {{ return elements; }} }};
            const top = topToolWindowZ({{ exclude: excluded, root, getStyle: (el) => el.style }});
            console.log(JSON.stringify({{ top }}));
            """
        )
    )

    assert values == {"top": 310}
