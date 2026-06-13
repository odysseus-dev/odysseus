"""classify_events must read the Memory `text` column, not a non-existent
`content` attribute.

The previous inline loop did `m.content`, which raised AttributeError on the
first Memory row; the surrounding except swallowed it, so the personal-context
block the LLM relies on was always empty. The logic now lives in
`_memory_context_lines`, which reads `text`.
"""
from src.builtin_actions import _memory_context_lines


class _Mem:
    def __init__(self, text):
        self.text = text


def test_uses_text_and_truncates_and_skips_blank():
    lines = _memory_context_lines([_Mem("Alice is my spouse"), _Mem("   "), _Mem("y" * 250)])
    assert lines[0] == "- Alice is my spouse"
    assert len(lines) == 2  # the blank row is skipped
    assert lines[1] == "- " + "y" * 200  # truncated to 200 chars


def test_skips_rows_without_text_attribute():
    class _Bad:  # mimics a schema where the attribute is absent
        pass

    assert _memory_context_lines([_Bad(), _Mem("ok")]) == ["- ok"]


def test_respects_limit():
    mems = [_Mem(f"memory {i}") for i in range(50)]
    assert len(_memory_context_lines(mems, limit=40)) == 40
