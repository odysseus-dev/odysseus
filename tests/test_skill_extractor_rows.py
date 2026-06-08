from services.memory import skill_extractor


def test_duplicate_title_skips_invalid_skill_rows():
    rows = [
        "bad-row",
        None,
        {"title": 123},
        {"title": "Small PR workflow"},
    ]

    assert skill_extractor._has_duplicate_title(rows, "small pr workflow")
    assert not skill_extractor._has_duplicate_title(rows, "release checklist")
