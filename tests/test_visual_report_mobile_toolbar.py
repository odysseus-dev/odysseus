from pathlib import Path

from bs4 import BeautifulSoup

from src.visual_report import generate_visual_report


SOURCE = (Path(__file__).resolve().parents[1] / "src/visual_report.py").read_text()


def test_export_bookmark_is_anchored_and_expands_on_demand():
    toolbar = SOURCE[SOURCE.index("/* ── Toolbar") : SOURCE.index("/* ── Hero ─")]
    assert "position: absolute;" in toolbar
    assert ".export-bookmark {{" in toolbar
    assert "width: 38px;" in toolbar
    assert ".export-bookmark.is-open {{ width: 108px; }}" in SOURCE


def test_hide_toolbar_is_an_export_menu_option_not_a_close_button():
    soup = BeautifulSoup(
        generate_visual_report("Toolbar report", "## Findings\n\nBody."),
        "html.parser",
    )

    hide_button = soup.select_one("#export-menu > #btn-hide-toolbar")
    assert hide_button is not None
    assert hide_button.get_text(strip=True) == "Hide toolbar"
    assert soup.select_one(".toolbar > .toolbar-close") is None
