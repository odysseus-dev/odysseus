"""Regression test: extract_memory_from_chat must not crash on bullet lines.

The fallback memory extractor (invoked by routes/memory_routes.py when the LLM
extractor fails) matched list items with ``r'^[-*•]|\\d+\\.\\s*(.*)'``. Because
of alternation precedence that pattern is ``(^[-*•]) | (\\d+\\.\\s*(.*))`` — the
capture group lives only in the numbered-list branch. A bullet line ("- ...")
matches the first branch, so ``group(1)`` is ``None`` and ``.strip()`` raised
``AttributeError``, crashing extraction for any assistant message that contains
a bullet list (the dominant case).
"""
from src.memory import MemoryManager


def test_extract_memory_from_chat_handles_bullets(tmp_path):
    mgr = MemoryManager(str(tmp_path))
    chat = [{
        "role": "assistant",
        "content": "- User likes coffee\n* Prefers tea in winter\n1. Wakes at 6am",
    }]

    out = mgr.extract_memory_from_chat(chat)
    texts = [m["text"] for m in out]

    assert "User likes coffee" in texts       # '-' bullet (used to crash)
    assert "Prefers tea in winter" in texts   # '*' bullet (used to crash)
    assert "Wakes at 6am" in texts            # numbered list (already worked)
