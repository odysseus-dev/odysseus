import pytest
from src.text_helpers import strip_think

def test_strip_think_cases():
    # 1. Mid-text unclosed leak (fails before fix)
    assert strip_think("Hello! <think> I am thinking.") == "Hello!"
    assert strip_think("Sure.\n<think>\nLet me reconsider...") == "Sure."
    assert strip_think("Sure.\n<thinking>\nLet me reconsider...") == "Sure."

    # 2. Start-anchored unclosed
    assert strip_think("<think> unclosed from start") == ""
    assert strip_think("   <thinking> thinking at start") == ""

    # 3. Closed block
    assert strip_think("Hello! <think> closed </think> Here is the answer.") == "Hello! Here is the answer."
    assert strip_think("Hello! <thinking> closed </thinking> Here is the answer.") == "Hello! Here is the answer."

    # 4. No-tag passthrough
    assert strip_think("No tags here.") == "No tags here."

    # 5. Content-before-opener preserved (part of mid-text unclosed)
    assert strip_think("Prefix text <think> trailing thoughts") == "Prefix text"
    
    # 6. Multiple blocks (closed + unclosed)
    assert strip_think("Hello! <think> closed </think> Here is the answer. <think> unclosed") == "Hello! Here is the answer."


def test_strip_think_handles_thought_tags():
    assert strip_think("<thought>internal reasoning</thought>Final answer.") == "Final answer."


def test_strip_think_handles_gemma4_thought_channel():
    text = "<|channel>thought\ninternal reasoning<channel|>Final answer."
    assert strip_think(text) == "Final answer."


def test_strip_think_handles_empty_gemma4_thought_channel():
    text = "<|channel>thought\n<channel|>Final answer."
    assert strip_think(text) == "Final answer."


def test_strip_think_unwraps_gemma4_response_channel():
    text = "<|channel>thought\ninternal reasoning<channel|><|channel>response\nFinal answer.<channel|>"
    assert strip_think(text) == "Final answer."
