"""Nested chat lists retain their HTML hierarchy and task markers."""
from html.parser import HTMLParser

import pytest

from tests.test_markdown_rendering_js import _run_markdown_case, node_available


class ListStructure(HTMLParser):
    def __init__(self, html):
        super().__init__()
        self.stack = []
        self.lists = []
        self.items = []
        self.feed(html)

    def handle_starttag(self, tag, attrs):
        if tag in {'ul', 'ol'}:
            self.lists.append((tag, sum(t in {'ul', 'ol'} for t in self.stack)))
            assert 'p' not in self.stack, 'a list must not be inside a paragraph'
        if tag == 'li':
            self.items.append((sum(t in {'ul', 'ol'} for t in self.stack), dict(attrs).get('class', '')))
        if tag not in {'br', 'hr', 'img', 'input', 'meta', 'link'}:
            self.stack.append(tag)

    def handle_endtag(self, tag):
        assert self.stack and self.stack[-1] == tag, f'unbalanced {tag}: {self.stack}'
        self.stack.pop()


@pytest.mark.parametrize('indent', ['  ', '   ', '    ', '\t'])
@pytest.mark.parametrize('marker,tag', [('-', 'ul'), ('*', 'ul'), ('1.', 'ol')])
def test_nested_items_stay_inside_the_parent_list(node_available, indent, marker, tag):
    html = _run_markdown_case(f'{marker} Parent\n{indent}{marker} Child\n{indent}{marker} Child two\n{marker} Sibling')
    structure = ListStructure(html)
    assert structure.lists == [(tag, 0), (tag, 1)]
    assert [depth for depth, _ in structure.items] == [1, 2, 2, 1]
    assert structure.stack == []


def test_mixed_nesting_dedents_and_ordered_siblings_keep_one_outer_list(node_available):
    html = _run_markdown_case('1. First\n2. Second\n   - Child\n     1. Grandchild\n   - Child two\n3. Third')
    structure = ListStructure(html)
    assert structure.lists == [('ol', 0), ('ul', 1), ('ol', 2)]
    assert [depth for depth, _ in structure.items] == [1, 1, 2, 3, 2, 1]


def test_nested_task_items_keep_independent_checked_markers(node_available):
    html = _run_markdown_case('- [ ] Parent\n  - [x] Child\n- [X] Complete parent\n  - [ ] Pending child')
    structure = ListStructure(html)
    assert [depth for depth, _ in structure.items] == [1, 2, 1, 2]
    assert [classes for _, classes in structure.items] == ['task-item', 'task-item task-done', 'task-item task-done', 'task-item']
    assert html.count('class="task-check"') == 4
    assert '- [ ]' not in html


def test_list_markers_do_not_consume_inline_emphasis_or_code(node_available):
    html = _run_markdown_case('* **Parent**\n  * *Child* with `code`\n* Sibling')
    structure = ListStructure(html)
    assert structure.lists == [('ul', 0), ('ul', 1)]
    assert '<strong>Parent</strong>' in html
    assert '<em>Child</em>' in html
    assert '<code>code</code>' in html


def test_indented_continuation_stays_with_its_list_item(node_available):
    html = _run_markdown_case('- Parent\n  continued text\n  - Child\n- Sibling')
    assert 'Parent<br>continued text<ul>' in html
    assert ListStructure(html).lists == [('ul', 0), ('ul', 1)]


def test_task_continuations_before_and_after_children_stay_in_task_text(node_available):
    html = _run_markdown_case('- [ ] Parent\n  continued\n  - Child\n  parent continuation\n- Sibling')
    assert 'Parent<br>continued</span><ul>' in html
    assert '</ul><span class="task-text">parent continuation</span></li>' in html
    assert [depth for depth, _ in ListStructure(html).items] == [1, 2, 1]


def test_changing_child_list_type_keeps_the_parent_open(node_available):
    html = _run_markdown_case('- Parent\n  - Bullet\n  1. Number\n- Sibling\n1. New ordered list')
    structure = ListStructure(html)
    assert structure.lists == [('ul', 0), ('ul', 1), ('ol', 1), ('ol', 0)]
    assert [depth for depth, _ in structure.items] == [1, 2, 2, 1, 1]


@pytest.mark.parametrize('source,lists,depths', [
    ('- One\n- Two', [('ul', 0)], [1, 1]),
    ('1. One\n2. Two', [('ol', 0)], [1, 1]),
    ('- [x] Done\n- [ ] Pending', [('ul', 0)], [1, 1]),
    ('Before\n\n- One\n\nAfter', [('ul', 0)], [1]),
    ('```text\n- Literal\n  - Also literal\n```', [], []),
])
def test_flat_lists_paragraphs_and_fenced_code_keep_existing_behavior(node_available, source, lists, depths):
    structure = ListStructure(_run_markdown_case(source))
    assert structure.lists == lists
    assert [depth for depth, _ in structure.items] == depths
