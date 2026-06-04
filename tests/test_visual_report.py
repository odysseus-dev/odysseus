from bs4 import BeautifulSoup

from src.visual_report import _md_to_html, _sanitize_report_html, generate_visual_report


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


def test_visual_report_markdown_sanitizes_active_html():
    html = _md_to_html(
        """
## Safe Heading

<script>alert(1)</script>
<iframe src="https://evil.example/embed"></iframe>
<svg><script>alert(2)</script></svg>

<p onclick="alert(3)" style="position:fixed" data-x="1">Body</p>
<a href="javascript:alert(5)" onclick="alert(6)">bad link</a>
"""
    )
    soup = BeautifulSoup(html, "html.parser")

    assert soup.find("script") is None
    assert soup.find("iframe") is None
    assert soup.find("svg") is None

    paragraph = soup.find("p", string="Body")
    assert paragraph.get_text(strip=True) == "Body"
    assert "onclick" not in paragraph.attrs
    assert "style" not in paragraph.attrs
    assert "data-x" not in paragraph.attrs

    link = soup.find("a", string="bad link")
    assert link is not None
    assert "href" not in link.attrs
    assert "onclick" not in link.attrs


def test_visual_report_sanitizer_strips_image_event_handlers():
    soup = BeautifulSoup(
        _sanitize_report_html(
            '<img src="https://example.com/a.png" onerror="alert(1)" '
            'style="width:1px" data-x="1" alt="ok">'
        ),
        "html.parser",
    )

    image = soup.find("img")
    assert image["src"] == "https://example.com/a.png"
    assert image["alt"] == "ok"
    assert "onerror" not in image.attrs
    assert "style" not in image.attrs
    assert "data-x" not in image.attrs


def test_visual_report_markdown_preserves_safe_markdown_features():
    html = _md_to_html(
        """
See https://example.com/path.

```python
print("ok")
```

| A | B |
|---|---|
| 1 | 2 |
"""
    )
    soup = BeautifulSoup(html, "html.parser")

    link = soup.find("a", href="https://example.com/path.")
    assert link is not None
    assert link["target"] == "_blank"
    assert "noopener" in link["rel"]
    assert soup.find("table") is not None
    assert soup.find("code") is not None
