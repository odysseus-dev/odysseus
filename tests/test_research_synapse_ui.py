from pathlib import Path

from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parents[1]


def test_active_research_visualizer_has_live_hierarchy_and_balanced_layout():
    source = (ROOT / "static/js/researchSynapse.js").read_text(encoding="utf-8")

    assert "Live research map" in source
    assert '<span class="rs-status">starting…</span>' in source
    assert "rs-root-halo" in source
    assert "const angles = [-90, 90, 180, 0" in source
    assert "rs-node-tone-${tone}" in source
    assert "if (phase) wrap.classList.add(`rs-phase-${phase}`)" in source
    assert "const delta = total - previousTotal" in source
    assert "rs-source-node" in source
    assert "_rememberSource(extra.title, extra.url)" in source


def test_active_research_visualizer_respects_reduced_motion():
    css = app_css()

    assert "@media (prefers-reduced-motion: reduce)" in css
    assert ".research-synapse .rs-live-dot" in css
    assert ".research-synapse .rs-guide ellipse" in css
