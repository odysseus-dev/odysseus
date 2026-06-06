"""Regression coverage for issue #2929: Gemma 4 thinking chat template.

Gemma 4 thinking models need the `<|think|>` control token in the system
instruction, while the generation prompt should start the model turn with the
thought channel. Cookbook serve commands should supply that template for
OpenAI-compatible servers instead of relying on a generic chat template that
cannot toggle thinking mode.
"""
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "static/js/cookbook.js"


def test_gemma4_thinking_template_uses_google_documented_thinking_placement():
    text = SRC.read_text(encoding="utf-8")

    assert "GEMMA4_THINKING_CHAT_TEMPLATE" in text
    assert "<|turn>system\\n<|think|>{{ message['content'] }}<turn|>" in text
    assert "<|turn>user" in text
    assert "<|turn>model" in text
    assert "<|turn>model\\n<|channel>thought" in text
    assert "<|turn>model\\n<|think|><|channel>thought" not in text


def test_vllm_and_sglang_apply_gemma4_thinking_template():
    text = SRC.read_text(encoding="utf-8")

    assert "function _isGemma4ThinkingModel" in text
    assert "const _gemma4ChatTemplate" in text
    assert "if (_gemma4ChatTemplate) cmd += ` --chat-template ${_gemma4ChatTemplate}`;" in text
    assert text.count("_gemma4ThinkingChatTemplateArg(modelName)") >= 2
