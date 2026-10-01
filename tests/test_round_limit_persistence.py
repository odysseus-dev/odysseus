"""Regression coverage for durable round-limit recovery state."""

from pathlib import Path

from routes.chat_routes import _with_round_limit_metadata


ROOT = Path(__file__).resolve().parent.parent
CHAT_JS = (ROOT / "static" / "js" / "chat.js").read_text(encoding="utf-8")
RENDERER_JS = (ROOT / "static" / "js" / "chatRenderer.js").read_text(encoding="utf-8")


def test_round_limit_metadata_marks_reply_incomplete_without_losing_metrics():
    metadata = _with_round_limit_metadata(
        {"model": "test-model", "output_tokens": 12},
        20,
    )

    assert metadata["model"] == "test-model"
    assert metadata["output_tokens"] == 12
    assert metadata["completion_status"] == "incomplete"
    assert metadata["incomplete_reason"] == "round_limit"
    assert metadata["rounds_exhausted"] == 20


def test_agent_stream_persists_round_limit_metadata_before_save():
    source = (ROOT / "routes" / "chat_routes.py").read_text(encoding="utf-8")

    assert 'elif data.get("type") == "rounds_exhausted":' in source
    assert "_rounds_exhausted_count = data.get(\"rounds\") or _max_rounds" in source
    assert "_metrics_to_save = _with_round_limit_metadata(" in source


def test_history_renderer_restores_round_limit_continue_control():
    assert "function appendRoundLimitIndicator(parent, wrap, metadata)" in RENDERER_JS
    assert "metadata?.completion_status === 'incomplete'" in RENDERER_JS
    assert "metadata?.incomplete_reason === 'round_limit'" in RENDERER_JS
    assert "metadata.rounds_exhausted" in RENDERER_JS
    assert "window.chatModule.setPendingContinue(wrap);" in RENDERER_JS
    assert "if (!lastMsgAi)" in RENDERER_JS
    assert "round-limit-recovery" in RENDERER_JS
    multi_round_path = RENDERER_JS.split("// --- Agent multi-bubble reconstruction", 1)[1].split(
        "// --- Wake-task / supervisor system check-in", 1
    )[0]
    assert "appendRoundLimitIndicator(" in multi_round_path


def test_round_limit_continue_prompt_uses_existing_hidden_continue_protocol():
    prompt = (
        "Your previous response was interrupted because it reached the step "
        "limit before finishing."
    )

    assert prompt in CHAT_JS
    assert prompt in RENDERER_JS
