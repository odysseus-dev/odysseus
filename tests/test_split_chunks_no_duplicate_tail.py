"""Regression: split_chunks must not emit a duplicate trailing chunk.

The loop advanced `i = j - overlap` even after `j` reached the end of the text,
so any text longer than (size - overlap) got an extra final chunk duplicating
the last `overlap` characters. That duplicate is indexed and keyword-scored
twice, so retrieve_personal_keyword returns the same tail content twice.
"""
from src.personal_docs import split_chunks


def test_no_duplicate_tail_chunk():
    chunks = split_chunks("x" * 1100, size=1000, overlap=200)
    assert [len(c) for c in chunks] == [1000, 300]


def test_no_chunk_is_contained_in_another():
    text = "\n".join(
        f"unique-line-{k:04d}-square-{k * k:08d}-cube-{k * k * k:012d}"
        for k in range(300)
    )
    chunks = split_chunks(text, size=1000, overlap=200)
    # The buggy version produced a final 200-char chunk fully inside the prior one.
    for a in range(len(chunks)):
        for b in range(len(chunks)):
            if a != b:
                assert chunks[a] not in chunks[b]


def test_overlap_is_preserved_between_chunks():
    chunks = split_chunks("x" * 1100, size=1000, overlap=200)
    # Second chunk starts 200 chars before the first one ended (offset 800).
    assert len(chunks) == 2 and chunks[1] == ("x" * 1100)[800:1100]


def test_short_text_single_chunk():
    assert split_chunks("hello world", size=1000, overlap=200) == ["hello world"]
