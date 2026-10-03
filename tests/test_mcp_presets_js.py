"""MCP preset catalog invariants (shared module used by the live Add Server form).

`static/js/mcpPresets.js` is the single source of truth for the preset dropdown
in settings.js `showMcpForm`. These pin the properties the UI depends on:

  * every preset can actually populate the form (name/command/args present),
  * preset names are unique — a duplicate would render two identical options
    that fill the same fields, making one unreachable,
  * `presetServerName()` yields a distinct, non-empty, URL/ID-safe slug for
    every preset (it becomes the saved server name).

Driven through `node --input-type=module` (same approach as the other *_js.py
tests); skips when `node` is not installed.
"""
import json
import shutil
import subprocess
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parent.parent
_MODULE = _REPO / "static" / "js" / "mcpPresets.js"
# Node ESM rejects bare Windows drive-letter specifiers (ERR_UNSUPPORTED_ESM_URL_SCHEME),
# so import relative to the repo root we run the subprocess in.
_MODULE_SPEC = "./" + _MODULE.relative_to(_REPO).as_posix()
_HAS_NODE = shutil.which("node") is not None


def _run(expr):
    js = (
        f"import {{ MCP_PRESETS, presetServerName }} from '{_MODULE_SPEC}';"
        f"console.log(JSON.stringify({expr}));"
    )
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=js, capture_output=True, text=True, cwd=str(_REPO), timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    return json.loads(proc.stdout.strip())


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_every_preset_can_fill_the_form():
    presets = _run("MCP_PRESETS.map(p => ({name:p.name, command:p.command, args:p.args, env:p.env}))")
    assert presets, "preset catalog is empty"
    for p in presets:
        assert p["name"], f"preset missing a name: {p}"
        assert p["command"], f"{p['name']} has no command"
        assert isinstance(p["args"], list) and p["args"], f"{p['name']} has no args"
        assert isinstance(p["env"], dict), f"{p['name']} env must be an object"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_preset_names_are_unique():
    names = _run("MCP_PRESETS.map(p => p.name)")
    dupes = {n for n in names if names.count(n) > 1}
    assert not dupes, f"duplicate preset names render as identical options: {sorted(dupes)}"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_server_name_slugs_are_distinct_and_safe():
    slugs = _run("MCP_PRESETS.map(p => presetServerName(p.name))")
    for s in slugs:
        assert s, "presetServerName produced an empty slug"
        assert s == s.lower(), f"slug not lowercase: {s!r}"
        assert all(c.isalnum() or c == "-" for c in s), f"slug has unsafe chars: {s!r}"
    dupes = {s for s in slugs if slugs.count(s) > 1}
    assert not dupes, f"presets collapse to the same server name: {sorted(dupes)}"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_unbrowse_preset_present_with_expected_shape():
    p = _run("MCP_PRESETS.find(p => p.name === 'Unbrowse (web)') || null")
    assert p is not None, "Unbrowse preset missing from the catalog"
    assert p["command"] == "npx"
    assert p["args"] == ["-y", "unbrowse@12.1.0", "mcp"]
    assert list(p["env"]) == ["UNBROWSE_API_KEY"]
    assert _run("presetServerName('Unbrowse (web)')") == "unbrowse-web"


@pytest.mark.skipif(not _HAS_NODE, reason="node binary not on PATH")
def test_server_name_slug_handles_separators():
    # "Email (IMAP/SMTP)" -> "email-imap-smtp" (parens and slash collapse)
    assert _run("presetServerName('Email (IMAP/SMTP)')") == "email-imap-smtp"
    assert _run("presetServerName('CalDAV (Radicale/Nextcloud)')") == "caldav-radicale-nextcloud"
