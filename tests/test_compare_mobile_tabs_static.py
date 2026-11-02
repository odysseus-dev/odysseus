from pathlib import Path
from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_mobile_compare_mounts_accessible_tabs_without_removing_panes():
    index = _read("static/js/compare/index.js")
    panes = _read("static/js/compare/panes.js")

    assert "mountMobilePaneTabs(container, grid, (anchor) => _addPane(anchor))" in index
    assert "role', 'tablist'" in panes
    assert "role', 'tab'" in panes
    assert "role', 'tabpanel'" in panes
    assert "aria-selected" in panes
    assert "aria-hidden" in panes
    assert "pane.classList.toggle('compare-pane-mobile-active'" in panes
    assert "pane.remove()" not in panes[panes.index("function activateMobilePane"):panes.index("function mountMobilePaneTabs")]


def test_mobile_compare_reconciles_tabs_after_pane_lifecycle_changes():
    panes = _read("static/js/compare/panes.js")

    assert "refreshMobilePaneTabs(i);" in panes
    assert "refreshMobilePaneTabs(Math.min(paneIdx, n - 1));" in panes
    assert "refreshMobilePaneTabs(paneIdx);" in panes
    assert "new MutationObserver(() => refreshMobilePaneTabs())" in panes


def test_mobile_compare_css_shows_only_the_active_card():
    css = app_css()
    mobile = css[css.index("/* Compare uses one full-width response card on phones."):]

    assert ".compare-mobile-tabs" in mobile
    assert "overflow-x: auto" in mobile
    assert ".compare-grid > .compare-pane.compare-pane-mobile-active" in mobile
    assert "display: none" in mobile
    assert "min-height: 60dvh" not in mobile


def test_probe_feedback_and_actions_use_the_shared_card_layout():
    selector = _read("static/js/compare/selector.js")
    css = app_css()

    assert "probeFeedback.className = 'compare-probe-feedback'" in selector
    assert "probeFeedback.appendChild(detail)" in selector
    assert "compare-probe-footer" in selector
    assert "compare-probe-start-anyway" in selector
    assert "retryBtn.innerHTML = '<svg" in selector
    assert "swapBtn.innerHTML = '<svg" in selector
    assert ".compare-probe-feedback:empty" in css
    assert ".compare-probe-start-anyway" in css
    assert "translateX(-2px)" in css[css.index("/* Compare uses one full-width response card on phones."):]
    assert 'compare-probe-title-label">Preround check:</span>' in selector
    assert 'compare-probe-title-status">Checking models...</span>' in selector
    assert ".compare-probe-title-status { font-weight: 400; }" in css
    assert "startAnywayBtn.innerHTML = _CMP_PLAY_ICON" in selector
    assert "/insufficient balance/i.test(errText) ? '$' : '!'" in selector
    assert ".compare-probe-detail-icon" in css
    assert "font: 600 11px/1 inherit" not in css


def test_probe_swap_reopens_and_highlights_the_failed_model_slot():
    selector = _read("static/js/compare/selector.js")
    css = app_css()

    assert "function _expandModelSlot(slotIdx)" in selector
    assert "row.dataset.slotIndex = String(idx);" in selector
    assert "cmp-model-picker-input" in selector
    assert "cmp-model-primary-select" in selector
    swap_handler = selector[selector.index("swapBtn.addEventListener"):selector.index("detail.appendChild(retryBtn)")]
    assert "renderModelRows();" in swap_handler
    assert "_expandModelSlot(idx);" in swap_handler
    assert ".cmp-model-row-swap-target" in css
