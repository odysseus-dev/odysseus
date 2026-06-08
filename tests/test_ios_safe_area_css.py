"""Regression: iOS PWA top chrome must respect safe-area-inset-top."""

from pathlib import Path

_CSS = (Path(__file__).resolve().parents[1] / "static" / "style.css").read_text(encoding="utf-8")
_INDEX = (Path(__file__).resolve().parents[1] / "static" / "index.html").read_text(encoding="utf-8")


def test_viewport_fit_cover_enables_safe_area_env():
    assert "viewport-fit=cover" in _INDEX


def test_safe_area_tokens_defined():
    assert "--safe-top: env(safe-area-inset-top, 0px)" in _CSS
    assert "--safe-bottom: env(safe-area-inset-bottom, 0px)" in _CSS


def test_mobile_hamburger_offsets_top_chrome():
    mobile_block = _CSS.split("@media (max-width:768px){", 1)[1]
    assert "top: calc(6px + var(--safe-top))" in mobile_block
    normalized = mobile_block.replace(" ", "")
    assert "padding-top:calc(42px+var(--safe-top))" in normalized
