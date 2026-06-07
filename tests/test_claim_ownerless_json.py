from scripts.claim_ownerless import claim_json_entries


def test_claim_json_entries_skips_invalid_rows():
    rows = [
        {"id": "a"},
        "bad-row",
        None,
        {"id": "b", "owner": "already"},
    ]

    assert claim_json_entries(rows, "admin") == 1
    assert rows == [
        {"id": "a", "owner": "admin"},
        "bad-row",
        None,
        {"id": "b", "owner": "already"},
    ]
