r"""Behaviour of the panel-loader registry in `static/js/panels.js`.

The registry is what keeps a lazily-loaded panel honest: it must import a
panel's module exactly once no matter how many times the user clicks, it must
fail loudly on a name nobody registered (rather than handing back `undefined`
and blowing up somewhere unrelated), and it must not memoise a *failed* load —
otherwise a panel that failed to load once while offline would stay broken for
the rest of the session.

`createPanelLoader` is exercised with stub thunks so these tests need no DOM and
no real panel module graph. The production `loadPanel` is the same function
applied to the real registry, checked here only for its registered names.
"""

import json
import os
import re
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_PANELS = (_REPO / "static" / "js" / "panels.js").as_posix()
_HAS_NODE = shutil.which("node") is not None

pytestmark = pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")


def _run(js: str) -> str:
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


def test_repeated_loads_import_once_and_share_one_promise():
    # A double-click on a rail button calls the loader twice before the first
    # import has resolved. Both calls must ride the same in-flight import.
    js = textwrap.dedent(
        f"""
        const {{ createPanelLoader }} = await import('{_PANELS}');
        let calls = 0;
        const load = createPanelLoader({{
          demo: () => {{ calls += 1; return Promise.resolve({{ open: () => 'opened' }}); }},
        }});
        const a = load('demo');
        const b = load('demo');
        const mod = await a;
        const c = load('demo');          // after it resolved, still cached
        console.log(JSON.stringify({{
          calls,
          sameWhilePending: a === b,
          sameAfterResolve: a === c,
          opened: (await c).open(),
          modOpened: mod.open(),
        }}));
        """
    )
    out = json.loads(_run(js))
    assert out["calls"] == 1
    assert out["sameWhilePending"] is True
    assert out["sameAfterResolve"] is True
    assert out["opened"] == "opened"
    assert out["modOpened"] == "opened"


def test_unknown_panel_throws_instead_of_returning_undefined():
    js = textwrap.dedent(
        f"""
        const {{ createPanelLoader }} = await import('{_PANELS}');
        const load = createPanelLoader({{ demo: () => Promise.resolve({{}}) }});
        let message = null, returned = 'not-reached';
        try {{ returned = load('nope'); }} catch (e) {{ message = e.message; }}
        console.log(JSON.stringify({{ message, returned: String(returned) }}));
        """
    )
    out = json.loads(_run(js))
    assert out["message"] is not None
    assert 'nope' in out["message"]
    assert out["returned"] == "not-reached"


def test_a_failed_load_is_not_memoised_so_a_retry_can_succeed():
    # First open happens offline and the import 404s; the second, back online,
    # must actually retry rather than replay the cached rejection.
    js = textwrap.dedent(
        f"""
        const {{ createPanelLoader }} = await import('{_PANELS}');
        let calls = 0;
        const load = createPanelLoader({{
          demo: () => {{
            calls += 1;
            return calls === 1
              ? Promise.reject(new Error('offline'))
              : Promise.resolve({{ open: () => 'opened' }});
          }},
        }});
        let firstError = null;
        try {{ await load('demo'); }} catch (e) {{ firstError = e.message; }}
        const second = await load('demo');
        console.log(JSON.stringify({{ calls, firstError, opened: second.open() }}));
        """
    )
    out = json.loads(_run(js))
    assert out["firstError"] == "offline"
    assert out["calls"] == 2
    assert out["opened"] == "opened"


def test_a_thunk_that_throws_synchronously_rejects_rather_than_escaping():
    js = textwrap.dedent(
        f"""
        const {{ createPanelLoader }} = await import('{_PANELS}');
        const load = createPanelLoader({{ demo: () => {{ throw new Error('boom'); }} }});
        let sync = null, rejected = null;
        let p;
        try {{ p = load('demo'); }} catch (e) {{ sync = e.message; }}
        try {{ await p; }} catch (e) {{ rejected = e.message; }}
        console.log(JSON.stringify({{ sync, rejected }}));
        """
    )
    out = json.loads(_run(js))
    assert out["sync"] is None, "a broken thunk must not throw at call time"
    assert out["rejected"] == "boom"


def test_the_image_editor_is_registered():
    js = textwrap.dedent(
        f"""
        const {{ panelNames }} = await import('{_PANELS}');
        console.log(JSON.stringify(panelNames()));
        """
    )
    assert "editor" in json.loads(_run(js))


# ── Offline coverage ──────────────────────────────────────────────────────
# A lazily-loaded panel is never fetched during a normal page load, so it only
# lands in the service-worker cache if sw.js precaches it explicitly. Miss one
# module and the panel opens fine online and dies offline — the failure mode is
# invisible until someone is on a plane. This walks the editor's import graph
# and pins that every file in it is listed in PANEL_PRECACHE, query string
# included (the SW matches on the full URL).

_SW = _REPO / "static" / "sw.js"
_JS_DIR = _REPO / "static" / "js"

_STATIC_IMPORT = re.compile(
    r"""(?:^|\n)\s*(?:import\s+(?:[^;'"()]*?\s+from\s+)?|export\s+(?:\*|\{[^}]*\})\s+from\s+)['"]([^'"]+)['"]"""
)


def _editor_module_graph() -> set[str]:
    """Every module statically reachable from galleryEditor.js, as '<path>[?query]'."""
    seen: set[str] = set()
    stack = ["galleryEditor.js"]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        source = (_JS_DIR / current.split("?")[0]).read_text(encoding="utf-8")
        for spec in _STATIC_IMPORT.findall(source):
            if not spec.startswith("."):
                continue
            path, _, query = spec.partition("?")
            target = os.path.normpath(
                os.path.join(os.path.dirname(current.split("?")[0]), path)
            )
            stack.append(f"{target}?{query}" if query else target)
    return seen


def _panel_precache_entries() -> set[str]:
    block = re.search(
        r"const PANEL_PRECACHE = \[(.*?)\];", _SW.read_text(encoding="utf-8"), re.S
    )
    assert block, "PANEL_PRECACHE not found in static/sw.js"
    return set(re.findall(r"'([^']+)'", block.group(1)))


def test_every_lazy_editor_module_is_precached_for_offline_use():
    graph = _editor_module_graph()
    precached = _panel_precache_entries()
    # Shared modules (ui.js, spinner.js, ...) are still eagerly loaded by
    # index.html, so they are cached by the normal page load. Only the part of
    # the graph that nothing else pulls in needs an explicit entry.
    lazy_only = {
        m for m in graph
        if m == "galleryEditor.js" or m.split("?")[0].startswith("editor/")
    }
    missing = {f"/static/js/{m}" for m in lazy_only} - precached
    assert not missing, (
        "these editor modules load lazily but are not in PANEL_PRECACHE, so the "
        f"editor would not open offline: {sorted(missing)}"
    )
