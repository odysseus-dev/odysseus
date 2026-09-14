"""OpenCode Go Responses-API routing — regression tests.

Go serves luna/grok/Muse Spark on .../v1/responses (OpenAI Responses API)
and the rest on .../v1/chat/completions. Sending a chat payload to a
responses-model returns HTTP 500. See https://opencode.ai/docs/go/#endpoints.
"""
from src import llm_core


class TestGoResponsesModelClassification:
    def test_responses_models(self):
        assert llm_core._is_opencode_go_responses_model("gpt-5.6-luna")
        assert llm_core._is_opencode_go_responses_model("opencode-go/gpt-5.6-luna")
        assert llm_core._is_opencode_go_responses_model("grok-4.6")
        assert llm_core._is_opencode_go_responses_model("muse-spark-1.3-contributor")
        assert llm_core._is_opencode_go_responses_model("muse-spark-1.2-contributor")

    def test_chat_models(self):
        assert not llm_core._is_opencode_go_responses_model("longcat-2.0")
        assert not llm_core._is_opencode_go_responses_model("kimi-k2.7-code")
        assert not llm_core._is_opencode_go_responses_model("glm-5.3-flash")
        assert not llm_core._is_opencode_go_responses_model("deepseek-v4-flash")
        assert not llm_core._is_opencode_go_responses_model("")
        assert not llm_core._is_opencode_go_responses_model(None)


class TestGoUrlNormalization:
    BASE = "https://opencode.ai/zen/go/v1"

    def test_responses_model_gets_responses_url(self):
        assert llm_core._normalize_opencode_go_url(self.BASE, "gpt-5.6-luna") == self.BASE + "/responses"

    def test_chat_model_gets_chat_url(self):
        assert llm_core._normalize_opencode_go_url(self.BASE, "longcat-2.0") == self.BASE + "/chat/completions"

    def test_stale_suffix_is_replaced(self):
        assert (
            llm_core._normalize_opencode_go_url(self.BASE + "/chat/completions", "gpt-5.6-luna")
            == self.BASE + "/responses"
        )
        assert (
            llm_core._normalize_opencode_go_url(self.BASE + "/responses", "longcat-2.0")
            == self.BASE + "/chat/completions"
        )


class TestGoResponsesPayload:
    MESSAGES = [
        {"role": "system", "content": "Be brief."},
        {"role": "user", "content": "Say OK"},
    ]

    def test_shape(self):
        p = llm_core._build_opencode_go_responses_payload("grok-4.6", self.MESSAGES, 0.2, 64, stream=True)
        assert p["model"] == "grok-4.6"
        assert p["stream"] is True
        assert p["store"] is False
        assert "instructions" in p and "Be brief" in p["instructions"]
        assert isinstance(p["input"], list) and p["input"]
        assert p["max_output_tokens"] == 64
        assert p["temperature"] == 0.2

    def test_temperature_omitted_for_restricted_model(self):
        # gpt-5.6-luna matches the gpt-5 fixed-temperature rule.
        p = llm_core._build_opencode_go_responses_payload("gpt-5.6-luna", self.MESSAGES, 0.0, 5)
        assert "temperature" not in p
        assert p["stream"] is False


class TestParseResponsesOutputText:
    def test_extracts_output_text(self):
        data = {
            "output": [
                {"type": "message", "content": [{"type": "output_text", "text": "Hel"},
                                                {"type": "output_text", "text": "lo"}]},
                {"type": "reasoning", "content": [{"type": "text", "text": "ignored?"}]},
            ]
        }
        assert llm_core._parse_responses_output_text(data) == "Hello"

    def test_empty(self):
        assert llm_core._parse_responses_output_text({}) == ""
        assert llm_core._parse_responses_output_text({"output": None}) == ""
