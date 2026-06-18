from bs4 import BeautifulSoup

from src.visual_report import _resolve_report_layout, generate_visual_report


def test_visual_report_toc_links_match_rendered_heading_ids():
    report = """
# Automated Crypto Trading Bot Strategies

### **1.0 Introduction & Research Scope**

Intro body.

### **2.0 Determining the "Best" Configuration**

Configuration body.
"""

    html = generate_visual_report(
        "crypto bot strategies",
        report,
        sources=[],
        stats={},
        session_id="rp-test",
    )
    soup = BeautifulSoup(html, "html.parser")

    links = soup.select(".toc-sidebar nav a")
    assert [link.get_text(strip=True) for link in links] == [
        "1.0 Introduction & Research Scope",
        '2.0 Determining the "Best" Configuration',
    ]

    for link in links:
        target_id = link["href"].removeprefix("#")
        target = soup.find(id=target_id)
        assert target is not None
        assert target.name in {"h2", "h3"}


def test_visual_report_print_css_keeps_pdf_readable():
    report = """
## Detailed Findings

Intro body.

| Mode | Intervals | Mood |
| --- | --- | --- |
| Ionian | W-W-H-W-W-W-H | Stable |
| Dorian | W-H-W-W-W-H-W | Minor with lift |
"""

    html = generate_visual_report(
        "musical modes",
        report,
        sources=[
            {
                "title": "Modes source",
                "url": "https://example.test/modes",
                "image": "https://example.test/modes.jpg",
            }
        ],
        stats={},
        session_id="rp-print-test",
        category="comparison",
    )
    soup = BeautifulSoup(html, "html.parser")
    style = soup.find("style").string

    assert "@page { margin: 12mm; }" in style
    assert style.rfind("@media print") > style.find(".category-comparison .content table")
    assert ".hero-image img" in style
    assert ".section-image img" in style
    assert "object-fit: contain !important" in style
    assert "max-height: 92mm !important" in style
    assert ".img-hide-btn" in style
    assert ".img-reroll-btn" in style
    assert ".content table" in style
    assert "display: table-header-group !important" in style
    assert "table-layout: fixed !important" in style


def test_visual_report_layout_presets_are_distinct_body_modes():
    html = generate_visual_report(
        "layout presets",
        "## Findings\n\nBody.",
        sources=[],
        stats={},
        report_layout="briefing",
    )
    soup = BeautifulSoup(html, "html.parser")
    body = soup.find("body")
    style = soup.find("style").string

    assert body["data-layout"] == "briefing"
    assert "layout-briefing" in body["class"]
    assert "body.layout-magazine .hero" in style
    assert "body.layout-briefing .content h2" in style
    assert "body.layout-paper .layout" in style
    assert "body.layout-atlas .layout" in style


def test_visual_report_auto_layout_uses_category_specific_presets():
    assert _resolve_report_layout("auto", "comparison") == "briefing"
    assert _resolve_report_layout("auto", "factcheck") == "paper"
    assert _resolve_report_layout("auto", "howto") == "atlas"
    assert _resolve_report_layout("auto", "landscape") == "magazine"
    assert _resolve_report_layout("classic", None) == "side_index"
