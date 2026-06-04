from bs4 import BeautifulSoup

from src.visual_report import generate_visual_report


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


def test_visual_report_toc_ignores_headings_inside_fenced_code():
    report = """
# Report Title

## Real Section

```markdown
## Fake Code Section
### Fake Code Detail
```

### Real Detail
"""

    html = generate_visual_report(
        "visual report anchors",
        report,
        sources=[],
        stats={},
        session_id="rp-fenced-heading-test",
    )
    soup = BeautifulSoup(html, "html.parser")

    links = soup.select(".toc-sidebar nav a")
    assert [link.get_text(strip=True) for link in links] == [
        "Real Section",
        "Real Detail",
    ]

    for link in links:
        target_id = link["href"].removeprefix("#")
        target = soup.find(id=target_id)
        assert target is not None
        assert target.get_text(" ", strip=True) == link.get_text(strip=True)


def test_visual_report_title_ignores_heading_inside_fenced_code():
    report = """
```python
# Not The Report Title
```

# Real Report Title

## Real Section
"""

    html = generate_visual_report(
        "fallback title",
        report,
        sources=[],
        stats={},
        session_id="rp-fenced-title-test",
    )
    soup = BeautifulSoup(html, "html.parser")

    assert soup.select_one(".hero h1").get_text(strip=True) == "Real Report Title"
