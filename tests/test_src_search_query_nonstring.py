"""Query helpers must tolerate non-string input.

`src.search.query` is a compatibility shim that aliases the canonical
`services.search.query`, so this exercises the live implementation.
"""
import services.search.query as q


def test_query_helpers_handle_non_string_queries():
    assert q._detect_question_type(None) is None
    assert q._split_multi_part(None) == []
    assert q._extract_site_filter(None) == ("", None)
    assert q._is_news_query(None) is False
    assert isinstance(q.enhance_query(None)[0], str)
    assert isinstance(q.build_enhanced_query(123), str)


def test_query_valid_query_still_works():
    assert q._detect_question_type("who is bob") == "who"
    assert q._is_news_query("latest news today") is True
    assert q._extract_site_filter("cats site:x.com")[1] == "x.com"
