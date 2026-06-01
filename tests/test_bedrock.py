"""Tests for native AWS Bedrock support.

Covers the pure translation layer in ``src.bedrock_client`` (no boto3 needed —
it is imported lazily inside the client builders) plus the ``llm_core``
dispatch and ``endpoint_resolver`` wiring, with ``bedrock_client`` mocked so no
AWS calls are made.
"""
import base64
import json

import pytest

from src import bedrock_client as bc


# ──────────────────────────────────────────────────────────────────────────
# region_from_url / parse_creds
# ──────────────────────────────────────────────────────────────────────────
class TestRegionFromUrl:
    def test_runtime_host(self):
        assert bc.region_from_url("https://bedrock-runtime.eu-west-3.amazonaws.com") == "eu-west-3"

    def test_control_plane_host(self):
        assert bc.region_from_url("https://bedrock.us-east-1.amazonaws.com") == "us-east-1"

    def test_default_when_unparseable(self):
        assert bc.region_from_url("https://example.com") == bc.DEFAULT_REGION

    def test_default_when_empty(self):
        assert bc.region_from_url("") == bc.DEFAULT_REGION


class TestParseCreds:
    def test_json_blob(self):
        creds = bc.parse_creds('{"access_key_id": "AK", "secret_access_key": "SK", "region": "us-east-1"}')
        assert creds["access_key_id"] == "AK"
        assert creds["secret_access_key"] == "SK"
        assert creds["region"] == "us-east-1"
        assert creds["session_token"] == ""

    def test_aws_prefixed_keys(self):
        creds = bc.parse_creds('{"aws_access_key_id": "AK", "aws_secret_access_key": "SK", "aws_session_token": "TT"}')
        assert creds["access_key_id"] == "AK"
        assert creds["secret_access_key"] == "SK"
        assert creds["session_token"] == "TT"

    def test_colon_form_with_token(self):
        creds = bc.parse_creds("AKIA:wsecret:tok")
        assert creds == {"access_key_id": "AKIA", "secret_access_key": "wsecret",
                         "session_token": "tok", "region": ""}

    def test_dict_input(self):
        creds = bc.parse_creds({"access_key_id": "AK", "secret_access_key": "SK"})
        assert creds["access_key_id"] == "AK"

    def test_empty_string_is_iam_fallback_shape(self):
        # All-empty credentials are the shape that makes _client() fall back to
        # the ambient AWS credential chain (env / shared config / IAM role).
        creds = bc.parse_creds("")
        assert creds == {"access_key_id": "", "secret_access_key": "",
                         "session_token": "", "region": ""}

    def test_none_is_iam_fallback_shape(self):
        creds = bc.parse_creds(None)
        assert all(v == "" for v in creds.values())

    def test_does_not_misparse_url(self):
        # A stray "https://..." must not be split on ':' into bogus keys.
        creds = bc.parse_creds("https://bedrock-runtime.us-east-1.amazonaws.com")
        assert creds["access_key_id"] == ""
        assert creds["secret_access_key"] == ""


# ──────────────────────────────────────────────────────────────────────────
# to_converse
# ──────────────────────────────────────────────────────────────────────────
class TestToConverse:
    def test_system_messages_extracted(self):
        system, conv = bc.to_converse([
            {"role": "system", "content": "be terse"},
            {"role": "user", "content": "hi"},
        ])
        assert system == [{"text": "be terse"}]
        assert conv == [{"role": "user", "content": [{"text": "hi"}]}]

    def test_multimodal_content(self):
        b64 = base64.b64encode(b"hello").decode()
        _, conv = bc.to_converse([
            {"role": "user", "content": [
                {"type": "text", "text": "look"},
                {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
            ]},
        ])
        blocks = conv[0]["content"]
        assert blocks[0] == {"text": "look"}
        assert blocks[1]["image"]["format"] == "png"
        assert blocks[1]["image"]["source"]["bytes"] == b"hello"

    def test_assistant_tool_calls(self):
        _, conv = bc.to_converse([
            {"role": "user", "content": "q"},
            {"role": "assistant", "tool_calls": [
                {"id": "t1", "function": {"name": "foo", "arguments": '{"x": 1}'}},
            ]},
        ])
        tool_use = conv[1]["content"][0]["toolUse"]
        assert tool_use == {"toolUseId": "t1", "name": "foo", "input": {"x": 1}}

    def test_tool_result_message(self):
        _, conv = bc.to_converse([
            {"role": "user", "content": "q"},
            {"role": "assistant", "tool_calls": [
                {"id": "t1", "function": {"name": "foo", "arguments": "{}"}},
            ]},
            {"role": "tool", "tool_call_id": "t1", "content": "42"},
        ])
        tool_result = conv[2]["content"][0]["toolResult"]
        assert tool_result["toolUseId"] == "t1"
        assert tool_result["content"] == [{"text": "42"}]

    def test_first_turn_normalized_to_user(self):
        # Converse requires the first turn to be a user turn.
        _, conv = bc.to_converse([{"role": "assistant", "content": "hi"}])
        assert conv[0]["role"] == "user"
        assert conv[1]["role"] == "assistant"

    def test_consecutive_same_role_merged(self):
        _, conv = bc.to_converse([
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
        ])
        assert len(conv) == 1
        assert conv[0]["content"] == [{"text": "a"}, {"text": "b"}]

    def test_empty_content_gets_placeholder(self):
        # Converse rejects empty content blocks.
        _, conv = bc.to_converse([{"role": "user", "content": ""}])
        assert conv[0]["content"] == [{"text": " "}]


# ──────────────────────────────────────────────────────────────────────────
# to_tool_config / _inference_config
# ──────────────────────────────────────────────────────────────────────────
class TestToolConfig:
    def test_translation(self):
        cfg = bc.to_tool_config([
            {"type": "function", "function": {
                "name": "f", "description": "d", "parameters": {"type": "object", "properties": {}}}},
        ])
        spec = cfg["tools"][0]["toolSpec"]
        assert spec["name"] == "f"
        assert spec["description"] == "d"
        assert spec["inputSchema"] == {"json": {"type": "object", "properties": {}}}

    def test_skips_non_function_tools(self):
        cfg = bc.to_tool_config([{"type": "other"}])
        assert cfg is None

    def test_none(self):
        assert bc.to_tool_config(None) is None

    def test_description_falls_back_to_name(self):
        cfg = bc.to_tool_config([{"type": "function", "function": {"name": "f"}}])
        assert cfg["tools"][0]["toolSpec"]["description"] == "f"


class TestInferenceConfig:
    def test_default_max_tokens_when_zero(self):
        cfg = bc._inference_config(1.0, 0)
        assert cfg["maxTokens"] == bc.DEFAULT_MAX_TOKENS

    def test_explicit_max_tokens(self):
        assert bc._inference_config(0.5, 256)["maxTokens"] == 256

    def test_temperature_clamped_to_bedrock_range(self):
        # Odysseus uses a 0..2 scale; Bedrock only accepts 0..1.
        assert bc._inference_config(1.5, 0)["temperature"] == 1.0
        assert bc._inference_config(-0.5, 0)["temperature"] == 0.0
        assert bc._inference_config(0.3, 0)["temperature"] == 0.3


# ──────────────────────────────────────────────────────────────────────────
# llm_core dispatch (bedrock_client mocked)
# ──────────────────────────────────────────────────────────────────────────
BEDROCK_URL = "https://bedrock-runtime.us-east-1.amazonaws.com"


def _carrier(creds='{"access_key_id": "AK", "secret_access_key": "SK"}'):
    return {bc.BEDROCK_CREDS_HEADER: creds}


class TestLlmCoreDispatch:
    def test_detect_provider(self):
        from src.llm_core import _detect_provider, _provider_label
        assert _detect_provider(BEDROCK_URL) == "bedrock"
        assert _provider_label(BEDROCK_URL) == "AWS Bedrock"

    def test_sync_dispatch(self, monkeypatch):
        from src import llm_core
        seen = {}

        def fake_converse(url, creds, model, messages, temperature, max_tokens, tools=None):
            seen.update(url=url, creds=creds, model=model)
            return "hello from bedrock"

        monkeypatch.setattr(bc, "converse", fake_converse)
        out = llm_core.llm_call(BEDROCK_URL, "anthropic.claude", [{"role": "user", "content": "hi"}],
                                headers=_carrier())
        assert out == "hello from bedrock"
        assert seen["url"] == BEDROCK_URL
        assert seen["model"] == "anthropic.claude"
        # The decrypted credential blob is forwarded from the carrier header.
        assert "AK" in seen["creds"]

    async def test_async_dispatch(self, monkeypatch):
        from src import llm_core
        monkeypatch.setattr(bc, "converse", lambda *a, **k: "async bedrock")
        out = await llm_core.llm_call_async(BEDROCK_URL, "m", [{"role": "user", "content": "hi"}],
                                            headers=_carrier())
        assert out == "async bedrock"

    async def test_streaming_dispatch(self, monkeypatch):
        from src import llm_core

        def fake_events(url, creds, model, messages, temperature, max_tokens, tools=None):
            yield ("delta", "Hel")
            yield ("delta", "lo")
            yield ("tool_calls", [{"id": "t1", "name": "foo", "arguments": "{}"}])
            yield ("usage", 10, 5)

        monkeypatch.setattr(bc, "converse_stream_events", fake_events)
        chunks = []
        async for chunk in llm_core.stream_llm(BEDROCK_URL, "m", [{"role": "user", "content": "hi"}],
                                               headers=_carrier()):
            chunks.append(chunk)
        joined = "".join(chunks)
        assert '"delta": "Hel"' in joined
        assert '"delta": "lo"' in joined
        assert '"type": "tool_calls"' in joined
        assert '"type": "usage"' in joined
        assert "data: [DONE]" in joined

    async def test_streaming_surfaces_error(self, monkeypatch):
        from src import llm_core

        def fake_events(*a, **k):
            yield ("error", "AWS rejected the credentials", 502)

        monkeypatch.setattr(bc, "converse_stream_events", fake_events)
        chunks = [c async for c in llm_core.stream_llm(BEDROCK_URL, "m",
                                                       [{"role": "user", "content": "hi"}], headers=_carrier())]
        joined = "".join(chunks)
        assert "event: error" in joined
        assert "AWS rejected the credentials" in joined


# ──────────────────────────────────────────────────────────────────────────
# endpoint_resolver wiring + credential-carrier containment
# ──────────────────────────────────────────────────────────────────────────
class TestEndpointResolver:
    def test_build_chat_url_passes_base_through(self, monkeypatch):
        from src import endpoint_resolver as er
        monkeypatch.setattr(er, "resolve_url", lambda u: u)
        # No /chat/completions or /v1/messages appended for Bedrock.
        assert er.build_chat_url(BEDROCK_URL) == BEDROCK_URL

    def test_build_headers_carries_creds_for_bedrock(self):
        from src import endpoint_resolver as er
        headers = er.build_headers('{"access_key_id": "AK"}', BEDROCK_URL)
        assert headers[bc.BEDROCK_CREDS_HEADER] == '{"access_key_id": "AK"}'
        assert "Authorization" not in headers  # SigV4, no bearer

    def test_carrier_only_on_bedrock_path(self):
        from src import endpoint_resolver as er
        from src.llm_core import _bedrock_creds
        # Non-Bedrock endpoints never produce the carrier header...
        openai_headers = er.build_headers("sk-test", "https://api.openai.com/v1")
        assert bc.BEDROCK_CREDS_HEADER not in openai_headers
        assert openai_headers["Authorization"] == "Bearer sk-test"
        # ...so the Bedrock creds extractor finds nothing to consume.
        assert _bedrock_creds(openai_headers) is None
