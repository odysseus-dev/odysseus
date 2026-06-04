from src.memory import MemoryManager


def test_validate_entries_skips_non_dict_rows(tmp_path):
    # Entries come from json.load on the user-editable memory.json. A hand-edit
    # that drops a bare string / number / null into the array made the old loop
    # do item assignment on a non-dict and raise TypeError, losing the whole
    # memory store. Bad rows are now skipped.
    m = MemoryManager(str(tmp_path))
    out = m._validate_entries([
        {"id": "a", "text": "real memory"},
        "corrupt-row",
        None,
        123,
    ])
    assert [e["id"] for e in out] == ["a"]
    # the surviving entry is still backfilled with required defaults
    assert out[0]["category"] == "fact"
    assert out[0]["source"] == "unknown"
