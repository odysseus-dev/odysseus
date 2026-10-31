"""Render chat Markdown into the fixed A4 PDF template used by ``create_pdf``.

The model decides *what* goes into the document; this module alone decides how
it looks, so small local models still produce a well-formatted file. Input is
untrusted model output: raw HTML is shown as literal text, nothing is fetched,
and only a whitelisted set of Markdown blocks is laid out.
"""

from __future__ import annotations

import functools
import io
from datetime import datetime
from html import escape
from html.parser import HTMLParser
from pathlib import Path
from typing import Optional

import markdown as markdown_lib
from reportlab.lib import colors
from reportlab.lib.enums import TA_LEFT
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle
from reportlab.lib.units import cm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfgen import canvas as pdf_canvas
from reportlab.platypus import (
    HRFlowable,
    KeepTogether,
    ListFlowable,
    ListItem,
    Paragraph,
    Preformatted,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

MAX_MARKDOWN_CHARS = 200_000
MAX_PAGES = 100
DEFAULT_TITLE = "Document"

_FONT_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts" / "dejavu"
_SANS = "DejaVuSans"
_MONO = "DejaVuSansMono"
# Shown in place of characters DejaVu cannot draw (mostly emoji), so the
# reader sees that something was there instead of a silent gap.
_MISSING_GLYPH = "□"

_INK = colors.HexColor("#1f2933")
_MUTED = colors.HexColor("#616e7c")
_RULE = colors.HexColor("#cbd2d9")
_CODE_BG = colors.HexColor("#f2f4f7")
_HEADER_BG = colors.HexColor("#e4e7eb")

_MARGIN = 2 * cm
# Platypus frames pad their content by 6pt on each side; align tables and the
# footer with the text, not with the raw margin.
_FRAME_PAD = 6
_CONTENT_LEFT = _MARGIN + _FRAME_PAD
_CONTENT_WIDTH = A4[0] - 2 * _CONTENT_LEFT
_CODE_WRAP = 86  # monospaced characters per line at 8.5pt inside the code box


class PdfExportError(ValueError):
    """The content cannot be turned into a PDF (empty, oversized, too long)."""


@functools.lru_cache(maxsize=1)
def _register_fonts() -> frozenset[int]:
    """Register the bundled DejaVu family once; return the drawable code points."""
    faces = {
        _SANS: "DejaVuSans.ttf",
        f"{_SANS}-Bold": "DejaVuSans-Bold.ttf",
        f"{_SANS}-Oblique": "DejaVuSans-Oblique.ttf",
        f"{_SANS}-BoldOblique": "DejaVuSans-BoldOblique.ttf",
        _MONO: "DejaVuSansMono.ttf",
        f"{_MONO}-Bold": "DejaVuSansMono-Bold.ttf",
    }
    for name, filename in faces.items():
        pdfmetrics.registerFont(TTFont(name, str(_FONT_DIR / filename)))
    pdfmetrics.registerFontFamily(
        _SANS, normal=_SANS, bold=f"{_SANS}-Bold",
        italic=f"{_SANS}-Oblique", boldItalic=f"{_SANS}-BoldOblique",
    )
    pdfmetrics.registerFontFamily(
        _MONO, normal=_MONO, bold=f"{_MONO}-Bold", italic=_MONO, boldItalic=f"{_MONO}-Bold",
    )
    sans = pdfmetrics.getFont(_SANS).face.charToGlyph
    mono = pdfmetrics.getFont(_MONO).face.charToGlyph
    return frozenset(sans) & frozenset(mono)


def _drawable(text: str) -> str:
    glyphs = _register_fonts()
    return "".join(
        ch if ord(ch) in glyphs or ch in "\n\t" else _MISSING_GLYPH
        for ch in text
        # Variation selectors and joiners only modify a preceding emoji.
        if not (0xFE00 <= ord(ch) <= 0xFE0F or ch == "‍")
    )


@functools.lru_cache(maxsize=1)
def _styles() -> dict[str, ParagraphStyle]:
    _register_fonts()
    body = ParagraphStyle(
        "body", fontName=_SANS, fontSize=10.5, leading=15, textColor=_INK,
        alignment=TA_LEFT, spaceAfter=6,
    )
    return {
        "body": body,
        "title": ParagraphStyle("title", parent=body, fontName=f"{_SANS}-Bold",
                                fontSize=22, leading=27, spaceAfter=4),
        "date": ParagraphStyle("date", parent=body, fontSize=9.5, textColor=_MUTED, spaceAfter=0),
        "h1": ParagraphStyle("h1", parent=body, fontName=f"{_SANS}-Bold", fontSize=16,
                             leading=20, spaceBefore=12, spaceAfter=6),
        "h2": ParagraphStyle("h2", parent=body, fontName=f"{_SANS}-Bold", fontSize=13.5,
                             leading=17, spaceBefore=10, spaceAfter=5),
        "h3": ParagraphStyle("h3", parent=body, fontName=f"{_SANS}-Bold", fontSize=11.5,
                             leading=15, spaceBefore=8, spaceAfter=4),
        "code": ParagraphStyle("code", parent=body, fontName=_MONO, fontSize=8.5, leading=11.5,
                               spaceAfter=0),
        "cell": ParagraphStyle("cell", parent=body, fontSize=9.5, leading=12.5, spaceAfter=0),
        "header_cell": ParagraphStyle("header_cell", parent=body, fontName=f"{_SANS}-Bold",
                                      fontSize=9.5, leading=12.5, spaceAfter=0),
        "quote": ParagraphStyle("quote", parent=body, leftIndent=14, textColor=_MUTED,
                                fontName=f"{_SANS}-Oblique"),
    }


class _Node:
    __slots__ = ("tag", "attrs", "children")

    def __init__(self, tag: str, attrs: Optional[dict] = None):
        self.tag = tag
        self.attrs = attrs or {}
        self.children: list = []  # _Node or str


class _TreeBuilder(HTMLParser):
    """Build a minimal tree from python-markdown's output.

    html.parser rather than an XML parser: Markdown passes HTML entities such
    as ``&copy;`` through, which XML would reject.
    """

    _VOID = {"br", "hr", "img"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.root = _Node("root")
        self._stack = [self.root]

    def handle_starttag(self, tag, attrs):
        node = _Node(tag, dict(attrs))
        self._stack[-1].children.append(node)
        if tag not in self._VOID:
            self._stack.append(node)

    def handle_endtag(self, tag):
        for index in range(len(self._stack) - 1, 0, -1):
            if self._stack[index].tag == tag:
                del self._stack[index:]
                return

    def handle_data(self, data):
        self._stack[-1].children.append(data)


def _markdown_tree(source: str) -> _Node:
    md = markdown_lib.Markdown(extensions=["tables", "fenced_code", "sane_lists"])
    # Untrusted input: with these removed, raw HTML is escaped as ordinary text
    # instead of passing through into the layout.
    md.preprocessors.deregister("html_block")
    md.inlinePatterns.deregister("html")
    builder = _TreeBuilder()
    builder.feed(md.convert(source))
    builder.close()
    return builder.root


def _plain(node) -> str:
    if isinstance(node, str):
        return node
    return "".join(_plain(child) for child in node.children)


def _inline(node) -> str:
    """ReportLab paragraph markup for a node's inline content."""
    if isinstance(node, str):
        return escape(_drawable(node), quote=False)
    inner = "".join(_inline(child) for child in node.children)
    if node.tag in ("strong", "b"):
        return f"<b>{inner}</b>"
    if node.tag in ("em", "i"):
        return f"<i>{inner}</i>"
    if node.tag == "code":
        return f'<font name="{_MONO}" backColor="#f2f4f7">{inner}</font>'
    if node.tag == "br":
        return "<br/>"
    if node.tag == "a":
        href = node.attrs.get("href") or ""
        if href and href != _plain(node):
            return f'{inner} <font color="#616e7c">({escape(_drawable(href), quote=False)})</font>'
        return inner
    if node.tag == "img":
        return escape(_drawable(node.attrs.get("alt") or ""), quote=False)
    return inner


def _wrap_code(text: str) -> str:
    lines = []
    for line in _drawable(text.rstrip("\n")).expandtabs(4).split("\n"):
        while len(line) > _CODE_WRAP:
            # Prefer a space in the last third of the line; otherwise hard-break.
            cut = line.rfind(" ", _CODE_WRAP * 2 // 3, _CODE_WRAP)
            cut = cut if cut > 0 else _CODE_WRAP
            lines.append(line[:cut].rstrip())
            line = "  " + line[cut:].lstrip()
        lines.append(line)
    return "\n".join(lines)


def _code_flowable(text: str, styles) -> Table:
    # Preformatted ignores a style's backColor, so the shaded box is a
    # one-cell table around it.
    box = Table([[Preformatted(_wrap_code(text), styles["code"])]], colWidths=[_CONTENT_WIDTH],
                hAlign="LEFT")
    box.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), _CODE_BG),
        ("BOX", (0, 0), (-1, -1), 0.5, _RULE),
        ("LEFTPADDING", (0, 0), (-1, -1), 8),
        ("RIGHTPADDING", (0, 0), (-1, -1), 8),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    return box


def _list_flowable(node: _Node, styles) -> ListFlowable:
    items = []
    for child in node.children:
        if isinstance(child, _Node) and child.tag == "li":
            items.append(ListItem(_blocks(child, styles) or [Paragraph("", styles["body"])]))
    ordered = node.tag == "ol"
    return ListFlowable(
        items,
        bulletType="1" if ordered else "bullet",
        start=int(node.attrs.get("start") or 1) if ordered else None,
        bulletFontName=_SANS,
        bulletFontSize=10.5 if ordered else 8,
        bulletFormat="%s." if ordered else None,
        leftIndent=18,
        spaceAfter=4,
    )


def _table_flowable(node: _Node, styles) -> Optional[Table]:
    rows: list[list] = []
    header_rows = 0

    def collect(section: _Node, header: bool):
        nonlocal header_rows
        for row in section.children:
            if isinstance(row, _Node) and row.tag == "tr":
                cells = [c for c in row.children if isinstance(c, _Node) and c.tag in ("th", "td")]
                is_header = header or all(c.tag == "th" for c in cells)
                style = styles["header_cell"] if is_header else styles["cell"]
                rows.append([Paragraph(_inline(c), style) for c in cells])
                header_rows += 1 if is_header else 0

    sections = [c for c in node.children if isinstance(c, _Node) and c.tag in ("thead", "tbody")]
    for section in sections or [node]:
        collect(section, section.tag == "thead")
    if not rows:
        return None
    width = max(len(r) for r in rows)
    rows = [r + [Paragraph("", styles["cell"])] * (width - len(r)) for r in rows]
    table = Table(rows, colWidths=[_CONTENT_WIDTH / width] * width,
                  repeatRows=header_rows, hAlign="LEFT")
    commands = [
        ("GRID", (0, 0), (-1, -1), 0.5, _RULE),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    if header_rows:
        commands.append(("BACKGROUND", (0, 0), (-1, header_rows - 1), _HEADER_BG))
    table.setStyle(TableStyle(commands))
    return table


def _blocks(node: _Node, styles) -> list:
    """Flowables for a container's block children; loose text becomes a paragraph."""
    flowables: list = []
    pending_inline: list = []

    def flush():
        markup = "".join(_inline(part) for part in pending_inline).strip()
        pending_inline.clear()
        if markup:
            flowables.append(Paragraph(markup, styles["body"]))

    for child in node.children:
        tag = child.tag if isinstance(child, _Node) else None
        if tag in (None, "strong", "b", "em", "i", "code", "a", "br", "img", "span"):
            pending_inline.append(child)
            continue
        flush()
        if tag in ("h1", "h2", "h3", "h4", "h5", "h6"):
            style = styles["h1" if tag == "h1" else "h2" if tag == "h2" else "h3"]
            flowables.append(Paragraph(_inline(child), style))
        elif tag == "p":
            markup = _inline(child).strip()
            if markup:
                flowables.append(Paragraph(markup, styles["body"]))
        elif tag in ("ul", "ol"):
            flowables.append(_list_flowable(child, styles))
        elif tag == "pre":
            flowables.extend([Spacer(1, 2), _code_flowable(_plain(child), styles), Spacer(1, 10)])
        elif tag == "table":
            table = _table_flowable(child, styles)
            if table is not None:
                flowables.extend([table, Spacer(1, 8)])
        elif tag == "blockquote":
            for inner in _blocks(child, styles):
                if isinstance(inner, Paragraph):
                    inner = Paragraph(inner.text, styles["quote"])
                flowables.append(inner)
        elif tag == "hr":
            flowables.append(HRFlowable(width="100%", thickness=0.5, color=_RULE,
                                        spaceBefore=6, spaceAfter=10))
        else:
            flowables.extend(_blocks(child, styles))
    flush()
    return flowables


class _NumberedCanvas(pdf_canvas.Canvas):
    """Defers page output so every footer can say "Page N of M"."""

    def __init__(self, *args, footer_title: str = "", **kwargs):
        super().__init__(*args, **kwargs)
        self._saved_pages: list[dict] = []
        self._footer_title = footer_title

    def showPage(self):
        self._saved_pages.append(dict(self.__dict__))
        self._startPage()

    def save(self):
        total = len(self._saved_pages)
        if total > MAX_PAGES:
            raise PdfExportError(f"Document would be {total} pages; the limit is {MAX_PAGES}.")
        for state in self._saved_pages:
            self.__dict__.update(state)
            self._draw_footer(total)
            super().showPage()
        super().save()

    def _draw_footer(self, total: int):
        width, _ = A4
        y = _MARGIN / 2
        self.setStrokeColor(_RULE)
        self.setLineWidth(0.5)
        self.line(_CONTENT_LEFT, y + 10, width - _CONTENT_LEFT, y + 10)
        self.setFillColor(_MUTED)
        self.setFont(_SANS, 8)
        self.drawString(_CONTENT_LEFT, y, self._footer_title[:90])
        self.drawRightString(width - _CONTENT_LEFT, y, f"Page {self._pageNumber} of {total}")


def render_markdown_pdf(title: str, markdown: str, *, created: Optional[datetime] = None) -> bytes:
    """Return an A4 PDF of ``markdown`` under ``title``.

    :raises PdfExportError: empty or oversized content, or too many pages.
    """
    source = str(markdown or "")
    if len(source) > MAX_MARKDOWN_CHARS:
        raise PdfExportError(
            f"Content is {len(source):,} characters; the limit is {MAX_MARKDOWN_CHARS:,}."
        )
    if not source.strip():
        raise PdfExportError("There is no content to put in the PDF.")

    styles = _styles()
    clean_title = " ".join(str(title or "").split()) or DEFAULT_TITLE
    when = created or datetime.now()
    header = [
        Paragraph(escape(_drawable(clean_title), quote=False), styles["title"]),
        Paragraph(f"{when.day} {when:%B %Y}", styles["date"]),
        HRFlowable(width="100%", thickness=1, color=_INK, spaceBefore=8, spaceAfter=14),
    ]
    story = [KeepTogether(header), *_blocks(_markdown_tree(source), styles)]

    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4, title=clean_title, author="Odysseus",
        leftMargin=_MARGIN, rightMargin=_MARGIN, topMargin=_MARGIN, bottomMargin=_MARGIN + 0.4 * cm,
    )
    doc.build(story, canvasmaker=functools.partial(_NumberedCanvas, footer_title=clean_title))
    return buffer.getvalue()
