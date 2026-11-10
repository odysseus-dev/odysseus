"""Research reports preserve TeX for the shared, vendored math renderer."""
from bs4 import BeautifulSoup
import pytest

from src.visual_report import _md_to_html, generate_visual_report


@pytest.mark.parametrize("source,tex,display", [
    (r"Value $x_i^2$.", "x_i^2", "false"),
    (r"Value \(x_i^2\).", "x_i^2", "false"),
    (r"Value $$\sum_i x_i$$.", r"\sum_i x_i", "true"),
    (r"Value \[\sum_i x_i\].", r"\sum_i x_i", "true"),
    ("$$a_i\n\nb_i$$", "a_i\n\nb_i", "true"),
    ("\\[a_i\n\nb_i\\]", "a_i\n\nb_i", "true"),
])
def test_math_source_survives_markdown(source, tex, display):
    soup = BeautifulSoup(_md_to_html(source), "html.parser")
    pending = soup.select(".ody-math-pending")
    assert len(pending) == 1
    assert pending[0].get_text() == tex
    assert pending[0]["data-display"] == display
    assert pending[0].find("em") is None


@pytest.mark.parametrize("source", [
    "`$x_i$`", "```text\n$x_i$\n```", "~~~text\n$x_i$\n~~~",
    "    $x_i$", "<code>$x_i$</code>", "<pre>$x_i$</pre>",
    "Prices range from $5 to $10.", "An escaped \\$5 costs less than $10.",
])
def test_code_and_currency_are_not_math(source):
    soup = BeautifulSoup(_md_to_html(source), "html.parser")
    assert not soup.select(".ody-math-pending")
    assert "$" in soup.get_text()


def test_html_attributes_are_not_rewritten_as_math():
    soup = BeautifulSoup(_md_to_html('<a title="$literal$">Value $x_i$</a>'), "html.parser")
    assert soup.a["title"] == "$literal$"
    assert soup.select_one("a .ody-math-pending").get_text() == "x_i"


def test_link_destinations_stay_literal_while_link_labels_can_contain_math():
    soup = BeautifulSoup(_md_to_html('[$x_i$](https://example.com/$literal$)'), "html.parser")
    assert soup.a["href"] == "https://example.com/$literal$"
    assert soup.select_one("a .ody-math-pending").get_text() == "x_i"


def test_math_source_is_inert_and_report_sanitizer_stays_active():
    soup = BeautifulSoup(_md_to_html(
        r'$\text{<img src=x onerror="alert(1)">}$'
        '\n\n<script>alert(2)</script><a href="javascript:alert(3)">link</a>'
    ), "html.parser")
    assert soup.select_one(".ody-math-pending").get_text() == r'\text{<img src=x onerror="alert(1)">}'
    assert soup.find("img") is None
    assert soup.find("script") is None
    assert not soup.select('a[href^="javascript:"]')


def test_heading_math_reaches_body_and_toc_without_changing_anchor_contract():
    soup = BeautifulSoup(generate_visual_report(
        "Formula report", "# Formula report\n\n## Formula $x_i$\n\nValue $x_i$.",
        session_id="rp-math",
    ), "html.parser")
    link = soup.select_one(".toc-sidebar nav a")
    heading = soup.find(id=link["href"][1:])
    assert heading["id"] == "formula-xi"
    for node in (link, heading):
        assert node.select_one(".ody-math-pending").get_text() == "x_i"
    assert soup.select_one("#mobile-section-select option").get_text() == "Formula x_i"


def test_unclosed_display_math_stays_readable():
    soup = BeautifulSoup(_md_to_html("$$unfinished\n\nA later paragraph."), "html.parser")
    assert not soup.select(".ody-math-pending")
    assert "unfinished" in soup.get_text()
    assert "A later paragraph." in soup.get_text()
