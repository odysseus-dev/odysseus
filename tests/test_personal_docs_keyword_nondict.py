from src.personal_docs import retrieve_personal_keyword


def test_retrieve_personal_keyword_skips_non_dict_rows():
    # A corrupted personal index can hold non-dict rows (partial write, bad
    # import). The old loop did f["chunks"] which raised TypeError on a str
    # row and aborted the whole search; now bad rows are skipped.
    index = [
        "bad-row",
        None,
        ["also", "bad"],
        {"name": "report.txt", "chunks": ["hello world from the quarterly report"]},
    ]
    out = retrieve_personal_keyword(index, "hello", k=5)
    assert out == ["[report.txt :: chunk 1]\nhello world from the quarterly report"]


def test_retrieve_personal_keyword_tolerates_missing_chunks_key():
    index = [{"name": "empty.txt"}, {"name": "doc.txt", "chunks": ["alpha beta gamma"]}]
    out = retrieve_personal_keyword(index, "beta", k=5)
    assert out == ["[doc.txt :: chunk 1]\nalpha beta gamma"]
