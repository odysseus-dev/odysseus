"""Regression: chatgpt_subscription helpers must tolerate malformed external
data (a null/scalar API field, or a non-dict message element).

- fetch_available_models: `data.get("models", [])` returns the [] default only
  when the key is absent. A present `{"models": null}` yields None, and the
  `for item in entries` loop then raises TypeError.
- build_responses_input: `msg.get("role")` assumed every message is a dict,
  but the content-part loop already guards with isinstance — a bare-string
  message element crashed the outer loop with AttributeError.
"""
import types

import pytest

from src import chatgpt_subscription as cs


def _fake_get(payload, status=200):
    def _get(*a, **k):
        r = types.SimpleNamespace()
        r.status_code = status
        r.json = lambda: payload
        return r
    return _get


# ---------------------------------------------------------------------------
# fetch_available_models
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("payload", [
    {"models": None},        # key present but null
    {"models": 5},           # key present but scalar
    {"models": "nope"},      # key present but string
    ["not", "a", "dict"],    # top-level non-dict
    42,                      # top-level scalar
])
def test_fetch_available_models_tolerates_bad_shapes(monkeypatch, payload):
    monkeypatch.setattr(cs.httpx, "get", _fake_get(payload))
    # Before the guard, {"models": null} raised TypeError: 'NoneType' is not iterable.
    assert cs.fetch_available_models("tok") == []


def test_fetch_available_models_parses_valid_payload(monkeypatch):
    payload = {"models": [
        {"slug": "gpt-5.5", "priority": 10},
        {"slug": "o4-mini", "priority": 5},
    ]}
    monkeypatch.setattr(cs.httpx, "get", _fake_get(payload))
    out = cs.fetch_available_models("tok")
    assert "gpt-5.5" in out and "o4-mini" in out


# ---------------------------------------------------------------------------
# build_responses_input
# ---------------------------------------------------------------------------

def test_build_responses_input_skips_non_dict_messages():
    # Bare strings mixed into the message list must not crash the loop.
    out = cs.build_responses_input(["hi", {"role": "user", "content": "hello"}, 5])
    assert len(out) == 1
    assert out[0]["role"] == "user"
    assert out[0]["content"][0]["text"] == "hello"


def test_build_responses_input_handles_list_content():
    msgs = [{"role": "assistant", "content": [
        {"text": "part-a"}, {"content": "part-b"}, "bare-part",
    ]}]
    out = cs.build_responses_input(msgs)
    assert out[0]["role"] == "assistant"
    assert "part-a" in out[0]["content"][0]["text"]
    assert out[0]["content"][0]["type"] == "output_text"


def test_build_responses_input_empty_and_none():
    assert cs.build_responses_input([]) == []
    assert cs.build_responses_input(None) == []
