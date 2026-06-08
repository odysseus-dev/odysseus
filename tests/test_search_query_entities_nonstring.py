from services.search.query import _extract_entities


def test_extract_entities_handles_non_string_query():
    # _detect_question_type already guards non-strings, but the function then
    # runs re.findall over `query` directly, which raises TypeError on a
    # non-string. A non-str query should yield no entities.
    assert _extract_entities(None) == {"names": [], "dates": []}
    assert _extract_entities(123) == {"names": [], "dates": []}


def test_extract_entities_still_finds_names_and_years():
    out = _extract_entities("What did Alice do in 2024")
    assert "Alice" in out["names"]
    assert "2024" in out["dates"]
