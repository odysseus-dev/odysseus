"""Node-driven coverage for modalManager's launcher toggle.

Clicking the launcher of an open window closes it. Skills and Memory share
#memory-modal, so the toggle must only close the view its launcher opens:
clicking Memory while Skills is showing has to fall through to the normal
handler, which switches the modal to Memory instead of closing it.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
MODAL_MANAGER = ROOT / "static" / "js" / "modalManager.js"
pytestmark = pytest.mark.skipif(not shutil.which("node"), reason="node binary not on PATH")


def _click_launcher(mode: str, launcher_id: str):
    """Show #memory-modal in `mode`, click `launcher_id`, report what the toggle did."""
    source = textwrap.dedent(
        f"""
        // Permissive stand-in for the DOM the imported modules touch at load.
        const any = () => new Proxy(function () {{}}, {{
          get: (t, k) => (k === Symbol.toPrimitive ? () => 0 : k === 'then' ? undefined : any()),
          apply: () => any(), construct: () => any(), set: () => true,
        }});
        const closed = [];
        const modal = {{
          id: 'memory-modal',
          dataset: {{ memoryMode: {json.dumps(mode)} }},
          classList: {{ contains: () => false }},
          querySelector: (sel) => (sel === '.modal-content' ? null : {{ click: () => closed.push(true) }}),
          getBoundingClientRect: () => ({{ width: 400, height: 300 }}),
        }};
        Object.assign(globalThis, {{
          window: globalThis, addEventListener() {{}}, requestAnimationFrame: () => 0,
          innerWidth: 1440, innerHeight: 900,
          localStorage: {{ getItem: () => null, setItem() {{}}, removeItem() {{}} }},
          MutationObserver: class {{ observe() {{}} disconnect() {{}} }},
          ResizeObserver: class {{ observe() {{}} disconnect() {{}} }},
          getComputedStyle: () => ({{ display: 'flex', visibility: 'visible', getPropertyValue: () => '' }}),
          CustomEvent: class {{}},
        }});
        const listeners = [];
        globalThis.document = new Proxy({{}}, {{ get: (t, k) => {{
          if (k === 'addEventListener') return (type, fn, capture) => listeners.push({{ type, fn, capture }});
          if (k === 'getElementById') return (id) => (id === 'memory-modal' ? modal : null);
          return any();
        }} }});
        await import({json.dumps(MODAL_MANAGER.as_uri())});
        const toggle = listeners.find(l => l.type === 'click' && l.capture === true);
        const ev = {{
          stopped: false,
          target: {{ closest: () => ({{ id: {json.dumps(launcher_id)} }}) }},
          preventDefault() {{}},
          stopImmediatePropagation() {{ this.stopped = true; }},
        }};
        toggle.fn(ev);
        console.log(JSON.stringify({{ stopped: ev.stopped, closed: closed.length > 0 }}));
        process.exit(0);
        """
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=source,
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip().splitlines()[-1])


def test_memory_launcher_switches_from_skills_instead_of_closing():
    assert _click_launcher("skills", "tool-memory-btn") == {"stopped": False, "closed": False}


def test_skills_launcher_switches_from_memory_instead_of_closing():
    assert _click_launcher("memory", "tool-skills-btn") == {"stopped": False, "closed": False}


@pytest.mark.parametrize(
    ("mode", "launcher_id"),
    [("memory", "tool-memory-btn"), ("skills", "tool-skills-btn")],
)
def test_launcher_closes_its_own_open_view(mode, launcher_id):
    assert _click_launcher(mode, launcher_id) == {"stopped": True, "closed": True}
