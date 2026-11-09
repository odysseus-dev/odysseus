from bs4 import BeautifulSoup

from src.visual_report import _source_evidence_summary, generate_visual_report


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


def test_visual_report_surfaces_evidence_profile_and_source_quality():
    sources = [
        {
            "title": "Official documentation",
            "url": "https://docs.example.com/guide",
            "source_kind": "primary",
            "source_score": 90,
            "source_reason": "official project documentation",
            "retrieval": "browser",
        },
        {
            "title": "Independent analysis",
            "url": "https://analysis.example.net/report",
            "source_kind": "secondary",
            "source_score": 70,
            "retrieval": "fetch",
        },
    ]

    summary = _source_evidence_summary(sources)
    assert summary == {
        "sources": 2,
        "domains": 2,
        "primary": 1,
        "browser": 1,
        "average_score": 80,
        "rated": 2,
    }

    soup = BeautifulSoup(
        generate_visual_report(
            "Evidence report",
            "## Findings\n\nEvidence-backed text.\n\n### Detail\n\nMore detail.",
            sources=sources,
            stats={},
            session_id="rp-evidence",
        ),
        "html.parser",
    )

    values = [node.get_text(strip=True) for node in soup.select(".evidence-value")]
    assert values == ["2", "2", "1", "80/100"]
    assert soup.select_one(".evidence-read-time").get_text(" ", strip=True) == "1 min read"
    assert soup.select_one(".source-badge.primary").get_text(strip=True) == "primary"
    assert soup.find(string="Rendered read") is not None
    assert soup.find(string="90/100") is not None


def test_visual_report_has_reading_progress_and_mobile_section_navigation():
    soup = BeautifulSoup(
        generate_visual_report(
            "Navigation report",
            "# Navigation report\n\n## First section\n\nBody.\n\n### Detail\n\nMore body.",
            sources=[],
            stats={},
        ),
        "html.parser",
    )

    assert soup.select_one(".reading-progress-bar") is not None
    options = soup.select("#mobile-section-select option")
    assert [option.get_text(strip=True) for option in options] == [
        "First section",
        "— Detail",
    ]
    assert [option["value"] for option in options] == ["first-section", "detail"]


def test_visual_report_wraps_editorial_sections_and_captions_source_images():
    soup = BeautifulSoup(
        generate_visual_report(
            "Visual report",
            "# Visual report\n\n## Opening\n\nBody.\n\n## Analysis\n\nMore body.",
            sources=[{
                "title": "Official release notes",
                "url": "https://example.com/releases",
                "image": "https://cdn.example.com/release-cover.jpg",
            }],
            stats={},
        ),
        "html.parser",
    )

    sections = soup.select("main.content > section.report-section")
    assert [section.h2.get_text(strip=True) for section in sections] == [
        "Opening",
        "Analysis",
    ]
    hero = soup.select_one("figure.hero-image")
    assert hero.img["alt"] == "Official release notes"
    assert hero.figcaption.get_text(" ", strip=True) == (
        "Official release notes example.com"
    )


def test_visual_report_parses_and_compacts_research_trace_details():
    report = """# Trace report

## Findings

Body.

<details markdown="1">
<summary>Research trace</summary>

### Planned Actions

- **Round 1** `planner` -> `web_search`

```text
Sources analyzed: 3
```

</details>
"""
    soup = BeautifulSoup(
        generate_visual_report(
            "Trace report",
            report,
            sources=[{"title": "Source", "url": "https://example.com/source"}],
            stats={},
        ),
        "html.parser",
    )

    trace = soup.select_one("details.research-trace")
    assert trace is not None
    assert trace.select_one("h3").get_text(strip=True) == "Planned Actions"
    assert trace.select_one("li").get_text(" ", strip=True).startswith("Round 1")
    assert trace.select_one("pre").get_text(strip=True) == "Sources analyzed: 3"
    support_summaries = [
        node.get_text(" ", strip=True)
        for node in soup.select(".sources-panel > details > summary")
    ]
    assert support_summaries == ["Sources (1)", "Research trace"]
    assert soup.select_one("main.content > .sources-panel > .research-trace") is trace


def test_visual_report_samples_hero_image_for_accent_palette():
    rendered = generate_visual_report(
        "Palette report",
        "# Palette report\n\n## Findings\n\nBody.",
        sources=[{
            "title": "Colorful source",
            "url": "https://example.com/source",
            "image": "https://cdn.example.com/colorful.jpg",
        }],
        stats={},
    )

    assert "function __applyImagePalette(url)" in rendered
    assert "__applyImagePalette(heroWrap.dataset.imgUrl)" in rendered


def test_visual_explanation_renders_constrained_diagram_as_inline_svg():
    report = r'''# How photosynthesis works

## Energy flow

Light energy moves through a short conversion chain.

```visual-diagram
{"title":"Energy conversion","layout":"flow","nodes":[{"label":"Sunlight","detail":"Incoming light energy"},{"label":"Chlorophyll","detail":"Captures photons"},{"label":"Stored energy","detail":"Sugars retain energy"}],"edges":[{"from":0,"to":1,"label":"absorbed by"},{"from":1,"to":2,"label":"converted into"}]}
```

The diagram summarizes the mechanism, while the surrounding prose carries citations.
'''

    soup = BeautifulSoup(
        generate_visual_report(
            "Explain photosynthesis visually",
            report,
            sources=[],
            stats={},
            category="visual",
            session_id="rp-visual",
            hidden_images=["https://cdn.example.com/hidden.jpg"],
        ),
        "html.parser",
    )

    assert "category-visual" in soup.body.get("class", [])
    figure = soup.select_one("figure.visual-diagram-flow")
    assert figure is not None
    assert figure.figcaption.get_text(strip=True) == "Energy conversion"
    assert figure.select_one("svg[role='img']")["aria-label"] == "Energy conversion"
    assert len(figure.select("g.visual-node")) == 3
    assert len(figure.select("path.visual-edge")) == 2
    assert "visual-diagram" not in soup.get_text()
    assert soup.select_one("#btn-restore-images") is None
    assert soup.select_one("#btn-hide-toolbar").get_text(strip=True) == "Hide toolbar"


def test_visual_explanation_escapes_diagram_copy_and_discards_invalid_specs():
    report = r'''# Safe diagrams

## Valid

```visual-diagram
{"title":"Safe <script>alert(1)</script>","layout":"layers","nodes":[{"label":"<img src=x onerror=alert(1)>","detail":"First"},{"label":"Second","detail":"Done"}]}
```

## Invalid

```visual-diagram
{"title":"Broken","nodes":[}
```
'''

    soup = BeautifulSoup(
        generate_visual_report(
            "Safe visual explanation",
            report,
            sources=[],
            stats={},
            category="visual",
        ),
        "html.parser",
    )

    assert len(soup.select("figure.visual-diagram")) == 1
    assert soup.select_one("figure.visual-diagram-layers") is not None
    assert soup.select_one("main.content script") is None
    assert soup.select_one("main.content img") is None
    assert "<img src=x onerror=alert(1)>" in soup.select_one(".visual-node-label").get_text()
    assert "Broken" not in soup.get_text()


def test_visual_explanation_embeds_sandboxed_visual_html_and_removes_active_content():
    report = r'''# How a black hole forms

```visual-html
<!doctype html>
<html><head><style>
body { background:#05070a; color:white; background-image:url(https://tracker.invalid/x); }
</style></head><body onclick="alert(1)">
<main><svg viewBox="0 0 400 200"><circle cx="200" cy="100" r="40"></circle></svg></main>
<a href="https://example.com">external</a>
<script>alert(1)</script>
</body></html>
```
'''

    soup = BeautifulSoup(
        generate_visual_report(
            "Explain black holes visually",
            report,
            sources=[],
            stats={},
            category="visual",
        ),
        "html.parser",
    )

    iframe = soup.select_one("figure.visual-html-story iframe")
    assert iframe is not None
    assert iframe.get("sandbox") == ["allow-same-origin"]
    assert "ResizeObserver" in iframe.get("onload", "")
    embedded = BeautifulSoup(iframe["srcdoc"], "html.parser")
    assert embedded.select_one("svg circle") is not None
    assert embedded.select_one("script") is None
    assert embedded.body.get("onclick") is None
    assert embedded.select_one("a").get("href") is None
    assert "tracker.invalid" not in str(embedded)


def test_visual_report_accepts_plain_html_fence_only_for_visual_category():
    report = """```html
<!doctype html><html><body><svg viewBox="0 0 20 20"><path d="M0 0h20v20z"/></svg></body></html>
```"""

    visual = generate_visual_report("A visual topic", report, [], {}, category="visual")
    standard = generate_visual_report("A standard topic", report, [], {}, category="standard")

    assert 'class="visual-html-story"' in visual
    assert 'class="visual-html-story"' not in standard
