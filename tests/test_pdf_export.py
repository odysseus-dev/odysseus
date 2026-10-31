"""Chat "make a pdf" export: Markdown rendered into the fixed A4 template."""

import io
from datetime import datetime

import pytest
from pypdf import PdfReader

from src.pdf_export import MAX_MARKDOWN_CHARS, PdfExportError, render_markdown_pdf

CREATED = datetime(2026, 10, 29, 14, 30)


def _render(markdown, title="Fix Windows boot error"):
    return render_markdown_pdf(title, markdown, created=CREATED)


def _reader(data):
    assert data.startswith(b"%PDF-")
    return PdfReader(io.BytesIO(data))


def _text(data):
    return "\n".join(page.extract_text() or "" for page in _reader(data).pages)


def _fonts(data):
    names = set()
    for page in _reader(data).pages:
        for font in (page["/Resources"].get("/Font") or {}).values():
            names.add(str(font.get_object()["/BaseFont"]))
    return names


def test_render_uses_a4_pages():
    page = _reader(_render("Hello")).pages[0]
    assert round(float(page.mediabox.width)) == 595
    assert round(float(page.mediabox.height)) == 842


def test_render_puts_title_and_date_on_first_page():
    first = _reader(_render("Body text.")).pages[0].extract_text()
    assert "Fix Windows boot error" in first
    assert "29 October 2026" in first


def test_render_keeps_hungarian_and_german_characters():
    text = _text(_render("Árvíztűrő tükörfúrógép. Grüße aus Potsdam, Straße."))
    assert "Árvíztűrő tükörfúrógép" in text
    assert "Grüße aus Potsdam, Straße" in text


def test_render_lays_out_steps_code_and_tables():
    markdown = (
        "## Steps\n\n"
        "1. Boot from the USB stick\n"
        "2. Open **Command Prompt**\n"
        "3. Run the repair\n\n"
        "- keep the *recovery key* nearby\n\n"
        "```\nbootrec /fixmbr\nbootrec /rebuildbcd\n```\n\n"
        "| Code | Meaning |\n|---|---|\n| 0xc0000001 | Missing boot device |\n"
    )
    data = _render(markdown)
    text = _text(data)
    for expected in ("Steps", "1.", "Boot from the USB stick", "2.", "Command Prompt",
                     "recovery key", "bootrec /fixmbr", "bootrec /rebuildbcd",
                     "0xc0000001", "Missing boot device"):
        assert expected in text
    # Inline Markdown markers are rendered, never printed.
    assert "**" not in text and "```" not in text and "|---|" not in text
    fonts = _fonts(data)
    assert any("DejaVuSansMono" in name for name in fonts)
    assert any("DejaVuSans-Bold" in name for name in fonts)


def test_render_numbers_pages_in_the_footer():
    long_markdown = "\n\n".join(f"Paragraph {i} " + "lorem ipsum " * 40 for i in range(60))
    reader = _reader(_render(long_markdown))
    total = len(reader.pages)
    assert total >= 2
    assert "Page 1 of %d" % total in reader.pages[0].extract_text()
    assert "Page %d of %d" % (total, total) in reader.pages[-1].extract_text()


def test_render_treats_raw_html_as_literal_text():
    text = _text(_render('Before <script>alert(1)</script> <a href="http://x">link</a> after'))
    assert "<script>alert(1)</script>" in text
    assert '<a href="http://x">' in text


def test_render_replaces_glyphs_the_font_lacks():
    text = _text(_render("Done 🎉 and checked ✅"))
    assert "Done" in text and "and checked" in text
    assert "🎉" not in text


def test_render_rejects_oversized_markdown():
    with pytest.raises(PdfExportError):
        _render("x" * (MAX_MARKDOWN_CHARS + 1))


def test_render_requires_some_content():
    with pytest.raises(PdfExportError):
        _render("   \n  ")


def test_render_falls_back_to_a_default_title():
    first = _reader(render_markdown_pdf("  ", "Body", created=CREATED)).pages[0].extract_text()
    assert "Document" in first
