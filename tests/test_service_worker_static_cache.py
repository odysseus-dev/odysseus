"""Service-worker cache policy for the /static/ scope.

The worker is registered at /static/sw.js without a scope, so it only controls
/static/ clients. The SPA root is outside that scope, which means an ordinary
page load never populates the worker's cache: anything the lazy editor graph
imports must be in the precache lists, shared modules included. The real
browser upgrade/offline proof lives in
tests/e2e/photo-editor/offline-upgrade.spec.js.
"""
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"
JS_DIR = STATIC / "js"
SW = (STATIC / "sw.js").read_text(encoding="utf-8")

_STATIC_IMPORT = re.compile(
    r"""(?:^|\n)\s*(?:import\s+(?:[^;'"()]*?\s+from\s+)?|export\s+(?:\*|\{[^}]*\})\s+from\s+)['"]([^'"]+)['"]"""
)


def _list(name: str) -> set[str]:
    block = re.search(rf"const {name} = \[(.*?)\];", SW, re.S)
    assert block, f"{name} not found in static/sw.js"
    return set(re.findall(r"'([^']+)'", block.group(1)))


def _fetch_handler() -> str:
    start = SW.index("self.addEventListener('fetch'")
    return SW[start:]


def _lazy_editor_graph() -> set[str]:
    panels = (JS_DIR / "panels.js").read_text(encoding="utf-8")
    entry = re.search(r"import\(['\"]\./(galleryEditor\.js(?:\?[^'\"]+)?)['\"]\)", panels)
    assert entry, "panels.js must lazily import galleryEditor.js"
    seen: set[str] = set()
    stack = [entry.group(1)]
    while stack:
        current = stack.pop()
        if current in seen:
            continue
        seen.add(current)
        source = (JS_DIR / current.split("?")[0]).read_text(encoding="utf-8")
        for spec in _STATIC_IMPORT.findall(source):
            if not spec.startswith("."):
                continue
            path, _, query = spec.partition("?")
            target = os.path.normpath(os.path.join(os.path.dirname(current.split("?")[0]), path))
            stack.append(f"{target}?{query}" if query else target)
    return seen


def test_every_module_in_the_lazy_editor_graph_is_precached_with_its_exact_url():
    graph = _lazy_editor_graph()
    assert len(graph) > 100, len(graph)
    precached = _list("PRECACHE") | _list("PANEL_PRECACHE")
    missing = sorted(f"/static/js/{module}" for module in graph if f"/static/js/{module}" not in precached)
    assert not missing, (
        "the editor imports these modules, but the /static/-scoped worker never "
        f"sees the SPA page load that would cache them: {missing}"
    )


def test_worker_keeps_the_static_scope():
    index = (STATIC / "index.html").read_text(encoding="utf-8")
    registrations = re.findall(r"serviceWorker\.register\(([^)]*)\)", index)
    assert registrations == ["'/static/sw.js?v=20260916autoscroll1'"]
    assert "Service-Worker-Allowed" not in (ROOT / "app.py").read_text(encoding="utf-8")


def test_fetch_handler_ignores_cross_origin_api_and_non_get_requests():
    handler = _fetch_handler()
    api_guard = handler.index("url.pathname.startsWith('/api/') || e.request.method !== 'GET') return;")
    origin_guard = handler.index("if (url.origin !== self.location.origin) return;")
    first_respond = handler.index("e.respondWith(")
    assert api_guard < origin_guard < first_respond


def test_offline_fallback_ignores_only_an_origin_vary_inside_the_current_cache():
    helper = SW[SW.index("async function cachedStaticFile(request)"):SW.index("self.addEventListener('fetch'")]
    assert "const cache = await caches.open(CACHE_NAME);" in helper
    assert "await cache.match(request);" in helper
    assert "cache.matchAll(request, { ignoreVary: true })" in helper
    assert ".every(name => name === 'origin')" in helper
    assert "|| Response.error();" in helper
    # No other lookup may relax Vary or search caches left by other versions.
    assert SW.count("ignoreVary") == 1
    assert "caches.match(" not in _fetch_handler()
    assert ".catch(() => cachedStaticFile(e.request))" in _fetch_handler()


def test_activation_removes_every_other_cache_version():
    assert re.search(r"const CACHE_NAME = 'odysseus-v\d+-[a-z0-9-]+';", SW)
    activate = SW[SW.index("self.addEventListener('activate'"):SW.index("async function cachedStaticFile")]
    assert "keys.filter(k => k !== CACHE_NAME).map(k => caches.delete(k))" in activate
    assert "self.clients.claim()" in activate
