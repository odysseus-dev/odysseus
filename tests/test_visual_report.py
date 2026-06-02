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


def test_visual_report_renders_all_math_delimiter_styles():
    """All four delimiter styles (chat-compatible) become self-contained MathML."""
    report = """# Math Coverage

## Results

Inline mass-energy $E = mc^2$ and GPT inline \\(x_1 + x_2\\).

A display sum:

$$
\\sum_{i=1}^{n} a_i \\cdot b_i
$$

GPT display:

\\[
\\int_0^1 f(x)\\,dx = \\frac{1}{2}
\\]

A matrix $$\\begin{pmatrix} a & b \\\\ c & d \\end{pmatrix}$$ rounds it out.
"""
    html = generate_visual_report("math demo", report, sources=[], stats={})

    # One <math> per span: 2 inline ($..$, \(..\)) + 3 display ($$..$$, \[..\], matrix).
    assert html.count("<math") == 5
    assert 'display="inline"' in html
    assert 'display="block"' in html
    # Matrix delimiters (`&` / `\\`) survived instead of being HTML-mangled.
    assert "<mtable" in html
    # Self-contained: math is server-rendered MathML, with no external/CDN
    # math library pulled in (the report CSP would block one anyway).
    assert "cdn.jsdelivr.net" not in html
    assert "<script src" not in html


def test_visual_report_math_not_mangled_into_emphasis():
    """`$a*b*c$` must render as math, not turn the `*` into <em> emphasis."""
    report = "# Notation\n\n## Body\n\nThe product $a*b*c$ is math, not emphasis.\n"
    html = generate_visual_report("q", report, sources=[], stats={})
    assert "<math" in html
    assert "<em>" not in html


def test_visual_report_math_inside_code_block_stays_literal():
    """Dollar spans inside fenced code are code, not math."""
    report = '# Code\n\n## Snippet\n\n```\nprice = "$x$ not math"\n```\n\nDone.\n'
    html = generate_visual_report("q", report, sources=[], stats={})
    assert "<math" not in html
    assert "$x$" in html


def test_visual_report_currency_prose_is_not_rendered_as_math():
    """Adjacent dollar amounts in prose must not be captured as a math span."""
    report = "# Pricing\n\n## Costs\n\nThe widget costs $5 and the deluxe is $10 total.\n"
    html = generate_visual_report("q", report, sources=[], stats={})
    assert "<math" not in html


def test_visual_report_currency_does_not_swallow_following_math():
    """A `$5` in prose must not open a span that closes on a later real `$math$`.

    Regression: "fee of $5 ... Inline $a*b*c$" once matched "$5 ... Inline $"
    as math and left "a*b*c$" for markdown to mangle into <em>.
    """
    report = "# T\n\n## S\n\nA fee of $5 is unrelated. Inline $a*b*c$ renders.\n"
    html = generate_visual_report("q", report, sources=[], stats={})
    assert "<em>" not in html              # `a*b*c` was not leaked to markdown
    assert html.count("<math") == 1        # exactly the real `$a*b*c$` span
    assert "is unrelated" not in _math_text(html)  # prose stayed out of math


def _math_text(html: str) -> str:
    """Concatenated text inside all <math> elements (for asserting what is/isn't math)."""
    import re

    return " ".join(re.findall(r"<math[\s\S]*?</math>", html))


def test_visual_report_math_in_heading_does_not_corrupt_tag():
    """Inline math in a heading must render as MathML in the body, not leak
    into the heading's id attribute.

    Regression: math placeholders ended up in the toc-generated id slug, and
    restoring them injected `<span><math>` into `id="..."`, producing a broken
    tag and a stray `">` plus a duplicated equation in the rendered page.
    """
    report = (
        "# Quaternions\n\n"
        "## Algebra\n\n"
        "### The Fundamental Rules: $i^2 = j^2 = k^2 = ijk = -1$\n\n"
        "Body text.\n\n"
        "#### Euler note with $e^{i\\pi} = -1$ inside\n\n"
        "More body.\n"
    )
    html = generate_visual_report("q", report, sources=[], stats={})
    soup = BeautifulSoup(html, "html.parser")

    # No heading id may contain markup — that's the corruption signature.
    for h in soup.find_all(["h1", "h2", "h3", "h4"]):
        assert "<" not in (h.get("id") or "")
        assert "odys-math" not in (h.get("id") or "")

    # The math renders inside the heading body as real MathML.
    h3 = soup.find("h3")
    assert h3 is not None
    assert h3.find("math") is not None
    assert "The Fundamental Rules:" in h3.get_text()
    # And the equation is not duplicated as a stray text leak. Body has the h3
    # and h4 math (2); the TOC sidebar covers only h2/h3, so it mirrors just the
    # h3 (1) — 3 <math> elements total, none duplicated.
    assert html.count("<math") == 3

    # The TOC sidebar typesets math too (no raw `$...$` LaTeX in the rail).
    toc = soup.select_one(".toc-sidebar")
    rules_link = next(
        a for a in toc.select("nav a") if "The Fundamental Rules" in a.get_text()
    )
    assert rules_link.find("math") is not None
    assert "$" not in rules_link.decode_contents()


def test_visual_report_math_falls_back_to_raw_latex_without_converter(monkeypatch):
    """If latex2mathml is unavailable, show legible raw LaTeX rather than crash."""
    monkeypatch.setattr("src.visual_report._latex_to_mathml", None)
    report = "# T\n\n## S\n\nThe value $x^2$ matters.\n"
    html = generate_visual_report("q", report, sources=[], stats={})
    assert "<math" not in html
    assert "odys-math-raw" in html
    assert "x^2" in html
