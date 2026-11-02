"""Regression coverage for the Cookbook trending-model official-only switch."""

from pathlib import Path

from tests.helpers.stylesheets import app_css


ROOT = Path(__file__).resolve().parent.parent
COOKBOOK = (ROOT / "static/js/cookbook.js").read_text(encoding="utf-8")
ROUTES = (ROOT / "routes/cookbook_routes.py").read_text(encoding="utf-8")


def test_trending_models_expose_persistent_official_only_switch():
    assert 'id="cookbook-hf-official-only"' in COOKBOOK
    assert 'aria-label="Show official models only"' in COOKBOOK
    assert "localStorage.getItem('cookbook_hf_official_only_v1')" in COOKBOOK
    assert "localStorage.setItem('cookbook_hf_official_only_v1'" in COOKBOOK
    assert "params.set('official_only', 'true')" in COOKBOOK
    assert "<span>Official only</span>" in COOKBOOK


def test_trending_models_list_stays_within_the_cookbook_window():
    style = app_css()

    rule = style[style.index("#cookbook-hf-latest-list {"):style.index("#cookbook-hf-latest-list {") + 220]
    assert "max-height: min(52vh, 480px);" in rule
    assert "overflow-y: auto;" in rule
    assert "overflow-x: hidden;" in rule
    assert "max-height:none;overflow:visible" not in COOKBOOK


def test_trending_endpoint_applies_first_party_namespace_filter():
    assert "official_only: bool = False" in ROUTES
    assert "if official_only and not _is_official(entry, repo_id):" in ROUTES
    assert "OFFICIAL_NAMESPACES =" in ROUTES


def test_official_only_toggle_fits_narrow_download_toolbar():
    style = app_css()
    start = style.rindex(".cookbook-official-filter {")
    rule = style[start:style.index("}", start)]
    assert "flex: 0 1 auto" in rule
    assert "min-width: 0" in rule
    assert "max-width: 100%" in rule
