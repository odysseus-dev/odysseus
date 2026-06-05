from services.memory import memory_extractor


def test_fingerprint_entries_skips_invalid_rows():
    value = memory_extractor._fingerprint_entries([
        {"id": "1", "text": "User likes small PRs.", "category": "preference"},
        "bad-row",
        None,
    ])

    expected = memory_extractor._fingerprint_entries([
        {"id": "1", "text": "User likes small PRs.", "category": "preference"},
    ])

    assert value == expected


def test_duplicate_check_skips_invalid_rows():
    existing = [
        "bad-row",
        {"text": "User likes small pull requests."},
        None,
    ]

    assert memory_extractor._is_text_duplicate("User likes small pull requests.", existing)
