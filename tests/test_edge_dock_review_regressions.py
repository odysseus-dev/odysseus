"""Structural integration coverage for PR #5840 review regressions."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MODAL_SNAP = (ROOT / "static/js/modalSnap.js").read_text(encoding="utf-8")
EMAIL = (ROOT / "static/js/emailLibrary.js").read_text(encoding="utf-8")
CSS = (ROOT / "static/style.css").read_text(encoding="utf-8")


def _section(source: str, start: str, end: str) -> str:
    return source.split(start, 1)[1].split(end, 1)[0]


def test_cleanup_counts_only_shared_usable_owner_discovery():
    cleanup = _section(
        MODAL_SNAP,
        "function _hasOtherDockedWindow(side, owner)",
        "function _hasAnyOtherDockedWindow(owner)",
    )
    assert "_dockOwnersForSide(side).some" in cleanup
    assert "querySelectorAll" not in cleanup


def test_split_width_sync_updates_every_left_owner_and_document_geometry():
    sync = _section(
        MODAL_SNAP,
        "export function syncDockSideWidth",
        "function _hasOtherDockedWindow",
    )
    assert "splitActive" in sync
    assert "owners.forEach" in sync
    assert "_applyDockWidthToOwner" in sync
    assert "_applyEmailDocSplitGeometry(left, w)" in sync
    assert "return requestedWidth" not in sync

    split_drag = _section(
        MODAL_SNAP,
        "const _dragTo = (clientX) =>",
        "stripe.addEventListener('pointerdown'",
    )
    assert "_dockOwnersForSide('left')" in split_drag
    assert "syncDockSideWidth('left', w, owners)" in split_drag

    apply_dock = _section(
        MODAL_SNAP,
        "function _applyDockInternal(modal, side, dockClass)",
        "function _onDockedModalGone",
    )
    assert "_activeDockVisualWidth(side)" in apply_dock


def test_legacy_email_lifecycle_uses_shared_sync_and_reconciliation():
    assert "reconcileDockSide, syncDockSideWidth" in EMAIL
    assert EMAIL.count("syncDockSideWidth('left'") >= 2
    assert EMAIL.count("reconcileDockSide('left'") >= 2
    assert "'.modal-left-docked, .email-snap-left'" in (
        ROOT / "static/js/edgeDockOwners.js"
    ).read_text(encoding="utf-8")


def test_switcher_uses_runtime_nav_geometry_and_scrolls_large_tab_stacks():
    left_rule = _section(CSS, ".edge-dock-switcher-left {", "}")
    assert "var(--icon-rail-w, 48px)" in left_rule
    assert "var(--sidebar-w, 0px)" in left_rule
    assert "var(--left-dock-visual-w" in left_rule
    assert "calc(48px" not in left_rule

    switcher_rule = _section(CSS, ".edge-dock-switcher {", "}")
    assert "max-height: calc(100dvh - 36px)" in switcher_rule
    assert "overflow-y: auto" in switcher_rule
    assert "overflow-x: hidden" in switcher_rule
    tab_rule = _section(CSS, ".edge-dock-switcher-tab {", "}")
    assert "flex: 0 0 124px" in tab_rule


def test_switcher_fronting_uses_canonical_top_level_tool_stack():
    assert "import { nextToolWindowZ } from './toolWindowZOrder.js'" in MODAL_SNAP
    fronting = _section(MODAL_SNAP, "const _bringOwnerToFront = (owner) =>", "const _simpleTitleForOwner")
    assert "nextToolWindowZ" in fronting
    assert "querySelectorAll('.modal" not in fronting
