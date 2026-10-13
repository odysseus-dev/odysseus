"""The settings sidebar footer that names the running build.

The admin panel had no way to answer "which build is this?" from the UI: the
version and the source commit were only reachable by hand-querying
``/api/version``. A footer pinned under the sidebar nav now shows the version
the build registers plus the commit its process loaded.

Three things can break independently, so each is covered on its own:

* ``formatSettingsBuildInfo`` decides what the two lines say, including what
  to do with the ``"unknown"`` the backend reports when it cannot resolve a
  commit. That is real logic, so it is exercised in node against the shipped
  module rather than asserted on as text.
* the footer's *position* is the requirement — below the scrolling nav, inside
  the sidebar. Only the markup's structure records that.
* the layouts that have no bottom-left to write in (the collapsed rail, the
  narrow tab rail) must hide it, and the ``[hidden]`` attribute only works
  here because the CSS restates it against the element's own ``display`` rule.
"""

import json
import re
import shutil
import subprocess
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
INDEX_HTML = (ROOT / "static" / "index.html").read_text(encoding="utf-8")
SIDEBAR_CSS = (
    ROOT / "static" / "css" / "cookbook-research-memory-settings.css"
).read_text(encoding="utf-8")

requires_node = pytest.mark.skipif(
    not shutil.which("node"), reason="node binary not on PATH"
)


def _format_build_info(payload):
    """Run the shipped formatter over one payload and return its result."""
    source = (
        "import { formatSettingsBuildInfo } from "
        "'./static/js/settings/sidebar.js';\n"
        f"console.log(JSON.stringify(formatSettingsBuildInfo({json.dumps(payload)})));\n"
    )
    result = subprocess.run(
        ["node", "--input-type=module", "-e", source],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    return json.loads(result.stdout)


# ---------------------------------------------------------------------------
# What the footer says
# ---------------------------------------------------------------------------


@requires_node
def test_shows_registered_version_and_short_commit():
    info = _format_build_info(
        {
            "version": "1.0.3",
            "build": "0.20.19",
            "source_commit": "8a28c5f9c0ffee1234567890abcdef1234567890",
        }
    )

    assert info["versionLabel"] == "v1.0.3 · build 0.20.19"
    assert info["commitLabel"] == "8a28c5f9"
    # The short hash is what fits the sidebar; the full one stays on hover.
    assert info["commitTitle"] == "8a28c5f9c0ffee1234567890abcdef1234567890"


@requires_node
def test_build_equal_to_version_is_not_printed_twice():
    info = _format_build_info(
        {"version": "1.0.3", "build": "1.0.3", "source_commit": "abcdef1234"}
    )

    assert info["versionLabel"] == "v1.0.3"


@requires_node
def test_unresolvable_commit_leaves_only_the_version():
    """`/api/version` reports the string "unknown" when `git rev-parse` fails
    — a read-only Docker tree with no `.git`. Printing it would be worse than
    printing nothing."""
    info = _format_build_info(
        {"version": "1.0.3", "build": "unknown", "source_commit": "unknown"}
    )

    assert info["versionLabel"] == "v1.0.3"
    assert info["commitLabel"] == ""
    assert info["commitTitle"] == ""


@requires_node
def test_commit_only_build_still_gets_a_footer():
    info = _format_build_info({"source_commit": "0123456789abcdef"})

    assert info["versionLabel"] == ""
    assert info["commitLabel"] == "01234567"


@requires_node
def test_non_sha_commit_override_is_left_intact():
    """`ODYSSEUS_SOURCE_COMMIT` can be any string; truncating a tag to eight
    characters would corrupt it, so only real hashes get shortened."""
    info = _format_build_info(
        {"version": "1.0.3", "source_commit": "release-2026-09-30"}
    )

    assert info["commitLabel"] == "release-2026-09-30"


@requires_node
@pytest.mark.parametrize(
    "payload",
    [
        {},
        {"version": "", "build": "", "source_commit": ""},
        {"version": "unknown", "build": "unknown", "source_commit": "unknown"},
    ],
)
def test_nothing_to_report_means_no_footer(payload):
    assert _format_build_info(payload) is None


# ---------------------------------------------------------------------------
# Where the footer sits
# ---------------------------------------------------------------------------


def _settings_sidebar_markup():
    start = INDEX_HTML.index('<div class="settings-sidebar">')
    end = INDEX_HTML.index('<div class="settings-panels">', start)
    return INDEX_HTML[start:end]


def test_footer_is_the_last_thing_in_the_sidebar():
    """The ask is bottom-left. The nav above it takes the free space, so the
    footer lands at the bottom by being the sidebar's final child — and it has
    to sit outside the nav's own scroll container to stay pinned there."""
    sidebar = _settings_sidebar_markup()

    nav_start = sidebar.index('<div class="settings-sidebar-content">')
    footer_start = sidebar.index('id="settings-sidebar-build"')
    assert nav_start < footer_start, "footer must come after the nav"

    # Nothing but the footer between the nav's closing tag and the sidebar's.
    tail = sidebar[footer_start:]
    assert '<div class="settings-nav' not in tail
    assert "settings-nav-item" not in tail


def test_footer_carries_both_fields_and_is_admin_gated():
    sidebar = _settings_sidebar_markup()
    footer = sidebar[sidebar.index('id="settings-sidebar-build"') - 200:]

    assert 'id="settings-sidebar-build-version"' in footer
    assert 'id="settings-sidebar-build-commit"' in footer
    # `.admin-only` is what syncAdminVisibility() hides for non-admins.
    assert "settings-sidebar-build admin-only" in footer
    # Hidden until /api/version answers, so no empty bordered strip appears.
    assert re.search(r'id="settings-sidebar-build"[^>]*\bhidden\b', footer)


# ---------------------------------------------------------------------------
# When the footer must disappear
# ---------------------------------------------------------------------------


def _rule_body(selector, css=SIDEBAR_CSS):
    match = re.search(
        re.escape(selector) + r"\s*\{([^}]*)\}", css
    )
    assert match, f"no rule for {selector}"
    return match.group(1)


def test_hidden_attribute_is_restated_against_the_display_rule():
    """`.settings-sidebar-build { display: flex }` is an author rule, so it
    outranks the UA stylesheet's `[hidden]` rule. Without this the attribute
    would not hide anything."""
    assert "display: flex" in _rule_body(".settings-sidebar-build")
    assert "display: none" in _rule_body(".settings-sidebar-build[hidden]")


def test_collapsed_sidebar_hides_the_footer():
    body = _rule_body(
        ".settings-sidebar.settings-sidebar-collapsed .settings-sidebar-build"
    )
    assert "display: none" in body


def test_both_narrow_tab_rail_layouts_hide_the_footer():
    """At narrow widths the sidebar becomes a horizontal rail, where a stacked
    footer has no bottom to sit at. Both the container query and the media
    query switch to that layout, so both have to hide it."""
    rails = re.findall(
        r"([^{}]*\.settings-sidebar-build[^{}]*)\{\s*display:\s*none;\s*\}",
        SIDEBAR_CSS,
    )
    tab_rail_rules = [r for r in rails if ".settings-sidebar-toggle" in r]
    assert len(tab_rail_rules) == 2, tab_rail_rules


# ---------------------------------------------------------------------------
# The payload the footer reads
# ---------------------------------------------------------------------------


def test_api_version_reports_version_build_and_commit():
    """The footer renders these three keys. `/api/version` is auth-exempt, so
    it answers without a session — the same way the login page already reads
    it."""
    from fastapi.testclient import TestClient

    from app import app

    with TestClient(app) as client:
        payload = client.get("/api/version").json()

    assert set(payload) >= {"version", "build", "source_commit"}
    # Absent values are reported as "unknown", never omitted or null, which is
    # the case formatSettingsBuildInfo() filters on.
    assert all(isinstance(payload[k], str) and payload[k] for k in payload)
