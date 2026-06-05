"""_parse_json_array must not inject the prompt's example queries.

The query-generation prompt ends with an Example: [...] array. Weak models
echo that example before emitting the real array. The old parser's greedy
regex spanned both arrays, failed to parse, and the repair fallback then
harvested EVERY quoted string from the reply, so the engine ran literal
searches for "query one" / "query two" / "query three".
"""

from src.deep_research import DeepResearcher


def _dr():
    # _parse_json_array only touches self via the static _strip_code_block,
    # so skip the heavy __init__.
    return object.__new__(DeepResearcher)


def test_example_echo_returns_only_the_real_array():
    text = (
        'Example: ["query one", "query two", "query three"]\n'
        '["impact of AI on jobs", "AI automation statistics 2026"]'
    )
    assert _dr()._parse_json_array(text) == [
        "impact of AI on jobs",
        "AI automation statistics 2026",
    ]


def test_truncated_real_array_after_example_skips_example():
    text = 'Example: ["query one", "query two"]\n["real query a", "real query b'
    assert _dr()._parse_json_array(text) == ["real query a"]


def test_plain_array_still_parses():
    assert _dr()._parse_json_array('["a", "b"]') == ["a", "b"]


def test_array_in_prose_still_parses():
    out = _dr()._parse_json_array('Here are the queries: ["a", "b"] hope that helps')
    assert out == ["a", "b"]


def test_truncated_single_array_still_repaired():
    out = _dr()._parse_json_array('["query one", "query two", "query thr')
    assert out == ["query one", "query two"]


def test_code_fenced_array_still_parses():
    assert _dr()._parse_json_array('```json\n["a", "b"]\n```') == ["a", "b"]


def test_no_array_returns_empty():
    assert _dr()._parse_json_array("no array here") == []
