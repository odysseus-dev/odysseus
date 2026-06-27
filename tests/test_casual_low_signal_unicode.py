r"""Regression: _is_casual_low_signal must count non-ASCII tail words as words.

The tail word-count used the ASCII-only class [A-Za-z0-9_'-]+, so a non-ASCII
greeting tail like "günaydın canım" (2 words) was shredded into 5 ASCII
fragments ("g","nayd","n","can","m"), pushing the count over the <=2 threshold.
Non-English greetings were therefore never recognised as casual/low-signal and
wrongly pulled memory/skills/RAG/context. The two identical copies
(routes/chat_helpers and src/agent_loop) now use the Unicode-aware [\w'-]+;
ASCII counting is byte-for-byte unchanged.
"""
import pytest

from routes.chat_helpers import _is_casual_low_signal as casual_chat
from src.agent_loop import _is_casual_low_signal as casual_agent

_COPIES = [
    pytest.param(casual_chat, id="chat_helpers"),
    pytest.param(casual_agent, id="agent_loop"),
]


@pytest.mark.parametrize("fn", _COPIES)
def test_non_ascii_greeting_tail_counts_as_words(fn):
    # opener "hey" + a 2-word Turkish tail -> casual / low-signal.
    # Before the fix the ASCII-only split shredded the tail into 5 fragments
    # (>2), so this returned False.
    assert fn("hey günaydın canım") is True


@pytest.mark.parametrize("fn", _COPIES)
def test_ascii_counting_unchanged(fn):
    assert fn("hey man") is True                       # 1-word tail -> casual
    assert fn("hey can you do this whole thing") is False  # long tail -> not casual
