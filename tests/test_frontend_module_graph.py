"""Every module the frontend imports must exist, and the document set must be cached.

There is no bundler here, so nothing resolves the import graph before a browser
does. A specifier that names a file which is not there is valid JavaScript:
``node --check`` passes, and ``test_frontend_module_version_parity.py`` checks
that a module is loaded under one URL identity without checking that the URL
leads anywhere. The failure surfaces as a blank panel at runtime, and in the
test suite as a scatter of unrelated browser tests going red at once with no
mention of the missing file.

That is affordable to close statically, so this closes it.
"""

import re
from pathlib import Path

from tests.helpers.document_source import document_source_paths

ROOT = Path(__file__).resolve().parents[1]
STATIC = ROOT / "static"

# Vendored third-party builds and the committed editor build output are not
# ours to reason about.
_SKIP_DIRS = ("lib/", "js/editor/build/")

# `import x from '...'`, `export ... from '...'`, and `import('...')`. Only
# quoted specifiers: a template literal is not statically resolvable, and the
# app does not use one.
_SPECIFIER = re.compile(
    r"""(?:^|[^\w.$])(?:import|export)\s*(?:[\w*{},\s$]*?\s*from\s*)?['"]([^'"]+)['"]"""
    r"""|\bimport\s*\(\s*['"]([^'"]+)['"]\s*\)""",
    re.M,
)


def _own_scripts() -> list[Path]:
    out = []
    for path in sorted(STATIC.rglob("*.js")):
        rel = path.relative_to(STATIC).as_posix()
        if any(rel.startswith(d) or f"/{d}" in rel for d in _SKIP_DIRS):
            continue
        out.append(path)
    return out


def _imports(path: Path):
    """(line, specifier, resolved path) for each relative/app-absolute import."""
    source = path.read_text(encoding="utf-8")
    for match in _SPECIFIER.finditer(source):
        specifier = match.group(1) or match.group(2)
        if not specifier:
            continue
        if not (specifier.startswith(".") or specifier.startswith("/static/")):
            continue  # bare specifier: not a file in this tree
        bare = specifier.split("?")[0].split("#")[0]
        if bare.startswith("/static/"):
            target = STATIC / bare.removeprefix("/static/")
        else:
            target = path.parent / bare
        line = source.count("\n", 0, match.start()) + 1
        yield line, specifier, target


def test_sources_are_discoverable() -> None:
    """Guard the guard: this must not pass by scanning nothing."""
    scripts = _own_scripts()
    assert len(scripts) > 100, len(scripts)
    total = sum(1 for p in scripts for _ in _imports(p))
    assert total > 300, total


def test_every_frontend_import_resolves_to_a_file() -> None:
    broken = [
        f"{path.relative_to(ROOT)}:{line} -> {specifier}"
        for path in _own_scripts()
        for line, specifier, target in _imports(path)
        if not target.is_file()
    ]

    assert not broken, "imports naming files that do not exist: " + repr(broken)


def test_document_implementation_set_is_precached() -> None:
    """A module extracted out of document.js must join the offline manifest.

    ``static/sw.js`` fetches the URLs it lists, nothing they in turn import, so
    a new module under ``static/js/document/`` is not cached just because the
    entry point that imports it is. Without it the editor breaks offline for
    anyone whose cache predates the split.
    """
    service_worker = (STATIC / "sw.js").read_text(encoding="utf-8")

    missing = []
    for path in document_source_paths():
        url = "/static/" + path.relative_to(STATIC).as_posix()
        if not re.search(rf"['\"]{re.escape(url)}(?:\?[^'\"]*)?['\"]", service_worker):
            missing.append(url)

    assert not missing, (
        "document editor modules absent from the sw.js precache lists: "
        f"{missing}"
    )
