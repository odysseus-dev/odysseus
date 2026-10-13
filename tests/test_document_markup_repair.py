from src.agent_tools.document_tools import _document_find_repair_hint


def test_minified_svg_spacing_error_returns_exact_source():
    tag = "<ellipse cx='50' cy='85' rx='8' ry='5' fill='black'/>"
    source = '<svg>' + '<circle/>' * 100 + tag + '</svg>'
    hint = _document_find_repair_hint(source, tag.replace('/>', ' />'), 0)
    assert repr(tag) in hint
    assert 'Copy an exact source fragment' in hint


def test_tag_boundaries_preserve_greater_than_inside_attribute():
    tag = '<path data-label="a > b" d="M 1 2"/>'
    assert repr(tag) in _document_find_repair_hint('<svg>' + tag + '</svg>', tag.replace('/>', ' />'), 0)


def test_unrelated_markup_does_not_invent_anchor():
    assert not _document_find_repair_hint('ordinary prose', '<circle fill="red"/>', 0)
