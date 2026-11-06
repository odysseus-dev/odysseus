"""Jev input must come from the latest user message only.

A free-text follow-up such as "test" must not replay an older question payload
that is still in the conversation history.
"""

import json

import pytest

from src import llm_core

JEV_INPUT = {
    "state": "sei al supermercato",
    "questions": {"cart": {"type": "noul", "instructions": "vedrai un unicorno?"}},
}


def test_extracts_input_from_latest_user_message():
    messages = [
        {"role": "user", "content": "```json\n" + json.dumps(JEV_INPUT) + "\n```"},
        {"role": "assistant", "content": "{\"answers\": {}}"},
        {"role": "user", "content": "test"},
    ]
    # Latest user message is free text: must not fall back to the older payload.
    with pytest.raises(ValueError, match="No valid Jev input"):
        llm_core._extract_typesafe_input(messages)


def test_extracts_input_when_latest_user_message_carries_it():
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "user", "content": json.dumps(JEV_INPUT)},
    ]
    assert llm_core._extract_typesafe_input(messages) == JEV_INPUT


def test_no_user_message_raises():
    with pytest.raises(ValueError, match="No valid Jev input"):
        llm_core._extract_typesafe_input([{"role": "assistant", "content": "hi"}])
