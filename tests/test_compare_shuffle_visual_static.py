from pathlib import Path
import re
from tests.helpers.stylesheets import app_css, stylesheet_cache_version


ROOT = Path(__file__).resolve().parent.parent


def _read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def test_compare_shuffle_shows_center_notice_with_dice_icon():
    panes = _read("static/js/compare/panes.js")
    css = app_css()

    assert "ICON_DICE" in panes
    assert "compare-shuffle-notice" in panes
    assert "Shuffling" in panes
    assert ".compare-shuffle-notice" in css
    assert "@keyframes compare-shuffle-dice" in css


def test_compare_chat_and_agent_panes_expose_per_pane_inference_settings():
    index = _read("static/js/compare/index.js")
    panes = _read("static/js/compare/panes.js")
    css = app_css()

    assert "pane-settings-btn" in panes
    assert "paneSettingsButtonHtml" in index
    assert "togglePaneSettings" in index
    assert "Thinking" in panes
    assert "Temperature" in panes
    assert "Max tokens" in panes
    assert "/generation-settings" in panes
    sync = index[index.index("function _syncCompareModeFromToolbar"):index.index("// ── closeCompare")]
    assert "document.querySelectorAll('.compare-pane .pane-mode-badge')" in sync
    assert "badge.remove()" in sync
    assert ".pane-settings-btn" in css


def test_compare_probe_control_has_requested_vertical_alignment():
    index = _read("static/js/compare/index.js")
    probe = _read("static/js/compare/probe.js")
    css = app_css()

    assert 'class="compare-check-icon"' in index
    assert '<span class="compare-check-label">Probe</span>' in index
    assert "createWhirlpool(14)" in probe
    assert "top: -2px" in css[css.index(".compare-check-label"):css.index(".compare-check-icon")]
    assert "top: 1px" in css[css.index(".compare-check-icon"):css.index("#compare-check-btn > .spinner-whirlpool")]
    assert "translateY(-2px)" in css[css.index("#compare-check-btn > .spinner-whirlpool"):]


def test_compare_agent_prompt_list_has_more_agentic_cases():
    icons = _read("static/js/compare/icons.js")

    for label in ["Primary source", "JS-heavy page", "Paper trail", "Debug + test", "CLI summarize", "Monte Carlo"]:
        assert label in icons


def test_compare_cache_key_bumped_for_shuffle_notice():
    html = _read("static/index.html")
    app = _read("static/app.js")
    index = _read("static/js/compare/index.js")

    app_versions = re.findall(r"/static/app\.js\?v=([A-Za-z0-9_-]+)", html)
    assert app_versions and len(set(app_versions)) == 1
    assert stylesheet_cache_version() == app_versions[0]
    assert re.search(r"compare/index\.js\?v=[A-Za-z0-9_-]+", app)
    assert re.search(r"vote\.js\?v=[A-Za-z0-9_-]+", index)
    assert re.search(r"panes\.js\?v=[A-Za-z0-9_-]+", index)
    assert re.search(r"selector\.js\?v=[A-Za-z0-9_-]+", index)


def test_compare_score_button_label_is_nudged_up():
    vote = _read("static/js/compare/vote.js")
    css = app_css()

    assert '<span class="compare-score-label">Score</span>' in vote
    assert ".compare-score-label" in css
    assert "top: -2px" in css[css.index(".compare-score-label"):css.index(".compare-vote-btn:hover")]
