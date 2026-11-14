"""Delimiter markers align the same column in headers and body rows."""
from html.parser import HTMLParser

import pytest

from tests.test_markdown_rendering_js import _run_markdown_case, node_available


class TableCells(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.tables = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag == 'table':
            self.tables.append([])
        elif tag == 'tr':
            self.tables[-1].append([])
        elif tag in {'th', 'td'}:
            style = dict(attrs).get('style', '')
            declarations = dict(part.strip().split(':', 1) for part in style.split(';') if ':' in part)
            self.tables[-1][-1].append(declarations.get('text-align', '').strip())


def tables(source):
    return TableCells(_run_markdown_case(source)).tables


@pytest.mark.parametrize('marker,expected', [(':---', 'left'), (':---:', 'center'), ('---:', 'right'), ('---', 'left')])
@pytest.mark.parametrize('outer_pipes', [True, False])
def test_each_marker_applies_to_header_and_all_body_rows(node_available, marker, expected, outer_pipes):
    rows = ['H1 | H2 | H3', f'{marker} | --- | ---', 'a | b | c', 'd | e | f']
    source = '\n'.join(f'| {row} |' if outer_pipes else row for row in rows)
    assert tables(source) == [[[expected, 'left', 'left']] * 3]


def test_empty_body_cells_keep_their_column_alignment(node_available):
    assert tables('| Left | Center | Right |\n| :--- | :---: | ---: |\n| A | | 123 |') == [
        [['left', 'center', 'right'], ['left', 'center', 'right']]
    ]


def test_alignment_does_not_carry_to_the_next_table(node_available):
    source = '| A | B |\n| :---: | ---: |\n| 1 | 2 |\n\n| C | D |\n| --- | --- |\n| 3 | 4 |'
    assert tables(source) == [
        [['center', 'right'], ['center', 'right']],
        [['left', 'left'], ['left', 'left']],
    ]


def test_single_hyphen_delimiters_support_gfm_alignment(node_available):
    assert tables('| A | B |\n| :-: | -: |\n| 1 | 2 |') == [
        [['center', 'right'], ['center', 'right']]
    ]


def test_inline_formatting_does_not_change_column_alignment(node_available):
    source = '| A | B |\n| :---: | ---: |\n| **bold** | `code` |'
    html = _run_markdown_case(source)
    assert TableCells(html).tables == [[['center', 'right'], ['center', 'right']]]
    assert '<strong>bold</strong>' in html
    assert '<code>code</code>' in html


@pytest.mark.parametrize('source', [
    '| A | B |\n| label: | :value |',
    '| A | B |\n| :: | :--:: |\n| 1 | 2 |',
])
def test_non_markers_keep_existing_left_alignment(node_available, source):
    assert all(align == 'left' for table in tables(source) for row in table for align in row)


def test_fenced_table_stays_literal(node_available):
    assert tables('```text\n| A | B |\n| :---: | ---: |\n| 1 | 2 |\n```') == []
