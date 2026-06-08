import pytest
from fastapi import HTTPException

from routes.chat_helpers import (
    _enforce_chat_privileges,
    clean_thinking_for_save,
    needs_auto_name,
    save_assistant_response,
)


class _AuthManager:
    def __init__(self, privileges):
        self._privileges = privileges

    def get_privileges(self, username):
        assert username == "alice"
        return self._privileges


class _Request:
    def __init__(self, privileges):
        self.app = type("App", (), {})()
        self.app.state = type("State", (), {"auth_manager": _AuthManager(privileges)})()


class _Session:
    def __init__(self, model):
        self.model = model


def test_allowed_models_legacy_empty_list_remains_unrestricted(monkeypatch):
    monkeypatch.setattr("routes.chat_helpers.get_current_user", lambda request: "alice")

    _enforce_chat_privileges(
        _Request({"allowed_models": [], "max_messages_per_day": 0}),
        _Session("provider/model-a"),
    )


def test_allowed_models_explicit_empty_restricted_list_blocks_all_models(monkeypatch):
    monkeypatch.setattr("routes.chat_helpers.get_current_user", lambda request: "alice")

    with pytest.raises(HTTPException) as exc:
        _enforce_chat_privileges(
            _Request({
                "allowed_models": [],
                "allowed_models_restricted": True,
                "max_messages_per_day": 0,
            }),
            _Session("provider/model-a"),
        )

    assert exc.value.status_code == 403
    assert "provider/model-a" in exc.value.detail


def test_allowed_models_nonempty_list_still_restricts_without_new_flag(monkeypatch):
    monkeypatch.setattr("routes.chat_helpers.get_current_user", lambda request: "alice")

    _enforce_chat_privileges(
        _Request({"allowed_models": ["provider/model-a"], "max_messages_per_day": 0}),
        _Session("provider/model-a"),
    )
    with pytest.raises(HTTPException):
        _enforce_chat_privileges(
            _Request({"allowed_models": ["provider/model-a"], "max_messages_per_day": 0}),
            _Session("provider/model-b"),
        )


class _FakeSession:
    def __init__(self, model="selected-model"):
        self.model = model
        self.history = []

    def add_message(self, message):
        self.history.append(message)


@pytest.mark.parametrize("name,expected", [
    # 24h format (the bug this PR fixes)
    ("deepseek-v4-flash 14:05:33", True),
    ("qwq 17:46:02", True),
    ("gemma3 23:59:59", True),
    ("claude-sonnet 4 0:00:00", True),

    # 12h format (was already working)
    ("deepseek-v4-flash 2:05:33 PM", True),
    ("qwq 06:46:02 AM", True),
    ("claude-sonnet-4 8:05:17 am", True),

    # empty / default
    ("", True),
    ("  ", False),
    ("Chat: something", True),

    # custom titles – should NOT trigger auto-naming
    ("custom title", False),
    ("CW Decoder for STM32", False),
    ("my chat about python", False),
    ("Fix the login bug", False),
])
def test_needs_auto_name(name, expected):
    assert needs_auto_name(name) == expected, f"needs_auto_name({name!r}) should be {expected}"


def test_clean_thinking_for_save_extracts_gemma4_thought_channel():
    content, metadata = clean_thinking_for_save(
        "<|channel>thought\ninternal reasoning<channel|>Final answer.",
        {"model": "google/gemma-4-31B-it"},
    )

    assert content == "Final answer."
    assert metadata["thinking"] == "internal reasoning"
    assert metadata["model"] == "google/gemma-4-31B-it"


def test_clean_thinking_for_save_strips_empty_gemma4_thought_channel():
    content, metadata = clean_thinking_for_save(
        "<|channel>thought\n<channel|>Final answer.",
        {"model": "google/gemma-4-31B-it"},
    )

    assert content == "Final answer."
    assert "thinking" not in metadata


def test_clean_thinking_for_save_unwraps_gemma4_response_channel():
    content, metadata = clean_thinking_for_save(
        "<|channel>thought\ninternal reasoning<channel|><|channel>response\nFinal answer.<channel|>",
        {"model": "google/gemma-4-31B-it"},
    )

    assert content == "Final answer."
    assert metadata["thinking"] == "internal reasoning"


def test_clean_thinking_for_save_extracts_thought_tag():
    content, metadata = clean_thinking_for_save(
        "<thought>internal reasoning</thought>Final answer.",
        {},
    )

    assert content == "Final answer."
    assert metadata["thinking"] == "internal reasoning"


def test_save_assistant_response_preserves_actual_and_requested_model():
    sess = _FakeSession("selected-model")

    save_assistant_response(
        sess,
        session_manager=None,
        session_id="s1",
        full_response="hello",
        last_metrics={"model": "actual-model", "input_tokens": 1, "output_tokens": 2},
        incognito=True,
    )

    assert sess.history[-1].metadata["requested_model"] == "selected-model"
    assert sess.history[-1].metadata["model"] == "actual-model"
