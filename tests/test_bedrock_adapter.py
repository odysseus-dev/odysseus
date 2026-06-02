"""Tests for the AWS Bedrock adapter — boto3 is mocked, no live AWS needed."""
import sys
import types
import asyncio

import pytest

from src import bedrock_adapter as ba


# ── Fake boto3 ──────────────────────────────────────────────────────────────

class _FakeClient:
    def __init__(self, service, region_name=None, **creds):
        self.service = service
        self.region_name = region_name
        self.creds = creds

    def list_foundation_models(self):
        return {"modelSummaries": [
            {"modelId": "anthropic.claude-3-5-sonnet-20240620-v1:0",
             "outputModalities": ["TEXT"], "inferenceTypesSupported": ["ON_DEMAND"]},
            {"modelId": "amazon.nova-pro-v1:0",
             "outputModalities": ["TEXT"], "inferenceTypesSupported": ["ON_DEMAND"]},
            {"modelId": "amazon.titan-image-generator-v1",
             "outputModalities": ["IMAGE"], "inferenceTypesSupported": ["ON_DEMAND"]},
            {"modelId": "meta.llama3-70b-provisioned",
             "outputModalities": ["TEXT"], "inferenceTypesSupported": ["PROVISIONED"]},
        ]}

    def converse(self, modelId=None, messages=None, system=None, inferenceConfig=None):
        self.last = dict(modelId=modelId, messages=messages, system=system, inferenceConfig=inferenceConfig)
        return {"output": {"message": {"content": [{"text": "Hello "}, {"text": "world"}]}},
                "usage": {"inputTokens": 5, "outputTokens": 2}}

    def converse_stream(self, modelId=None, messages=None, system=None, inferenceConfig=None):
        return {"stream": [
            {"contentBlockDelta": {"delta": {"text": "Hel"}}},
            {"contentBlockDelta": {"delta": {"text": "lo"}}},
            {"metadata": {"usage": {"inputTokens": 5, "outputTokens": 2}}},
        ]}


def _install_fake_boto3(monkeypatch, client_cls=_FakeClient):
    fake = types.ModuleType("boto3")
    fake.client = lambda service, **kw: client_cls(service, **kw)
    monkeypatch.setitem(sys.modules, "boto3", fake)


# ── URL / creds parsing ──────────────────────────────────────────────────────

class TestParsing:
    def test_is_bedrock_url(self):
        assert ba.is_bedrock_url("bedrock://us-east-1") is True
        assert ba.is_bedrock_url("https://api.openai.com/v1") is False
        assert ba.is_bedrock_url(None) is False

    def test_parse_region(self):
        assert ba.parse_region("bedrock://us-east-1") == "us-east-1"
        assert ba.parse_region("bedrock://eu-west-1") == "eu-west-1"

    def test_parse_creds_default_chain_when_blank(self):
        assert ba.parse_creds("") == {}
        assert ba.parse_creds(None) == {}

    def test_parse_creds_access_secret(self):
        c = ba.parse_creds("AKIA123:secretval")
        assert c["aws_access_key_id"] == "AKIA123"
        assert c["aws_secret_access_key"] == "secretval"
        assert "aws_session_token" not in c

    def test_parse_creds_with_session_token(self):
        c = ba.parse_creds("AKIA123:secretval:tok")
        assert c["aws_session_token"] == "tok"

    def test_creds_from_headers(self):
        assert ba.creds_from_headers({"Authorization": "Bearer AKIA:secret"}) == "AKIA:secret"
        assert ba.creds_from_headers({}) is None


# ── Model discovery ──────────────────────────────────────────────────────────

class TestListModels:
    def test_filters_to_text_on_demand_and_keeps_ids(self, monkeypatch):
        _install_fake_boto3(monkeypatch)
        models = ba.list_models("bedrock://us-east-1", "AKIA:secret")
        assert models == [
            "anthropic.claude-3-5-sonnet-20240620-v1:0",
            "amazon.nova-pro-v1:0",
        ]
        # image-only and provisioned-only models are excluded
        assert "amazon.titan-image-generator-v1" not in models
        assert "meta.llama3-70b-provisioned" not in models


# ── Message mapping ──────────────────────────────────────────────────────────

class TestToConverse:
    def test_system_extracted_and_turns_alternate(self):
        msgs = [
            {"role": "system", "content": "be brief"},
            {"role": "user", "content": "hi"},
            {"role": "assistant", "content": "hello"},
            {"role": "user", "content": "more"},
        ]
        system, conv = ba._to_converse(msgs)
        assert system == [{"text": "be brief"}]
        assert [m["role"] for m in conv] == ["user", "assistant", "user"]
        assert conv[0]["content"] == [{"text": "hi"}]

    def test_drops_empty_and_merges_same_role(self):
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "user", "content": "b"},
            {"role": "assistant", "content": None},
        ]
        _, conv = ba._to_converse(msgs)
        assert conv == [{"role": "user", "content": [{"text": "a"}, {"text": "b"}]}]

    def test_first_turn_forced_to_user(self):
        msgs = [{"role": "assistant", "content": "stray"}, {"role": "user", "content": "hi"}]
        _, conv = ba._to_converse(msgs)
        assert conv[0]["role"] == "user"

    def test_tool_message_folded_to_user(self):
        msgs = [{"role": "user", "content": "q"},
                {"role": "assistant", "content": "calling"},
                {"role": "tool", "content": "result"}]
        _, conv = ba._to_converse(msgs)
        assert conv[-1]["role"] == "user"
        assert conv[-1]["content"] == [{"text": "result"}]


# ── Chat (non-streaming) ─────────────────────────────────────────────────────

class TestChat:
    def test_chat_joins_text_blocks(self, monkeypatch):
        _install_fake_boto3(monkeypatch)
        out = ba.chat("bedrock://us-east-1", "amazon.nova-pro-v1:0",
                      [{"role": "user", "content": "hi"}], temperature=0.2, max_tokens=64,
                      api_key="AKIA:secret")
        assert out == "Hello world"


# ── Chat (streaming) ─────────────────────────────────────────────────────────

def _collect(agen):
    async def run():
        return [c async for c in agen]
    return asyncio.run(run())


class TestStream:
    def test_stream_emits_deltas_usage_and_done(self, monkeypatch):
        _install_fake_boto3(monkeypatch)
        chunks = _collect(ba.stream("bedrock://us-east-1", "amazon.nova-pro-v1:0",
                                    [{"role": "user", "content": "hi"}], api_key="AKIA:secret"))
        joined = "".join(chunks)
        assert '"delta": "Hel"' in joined
        assert '"delta": "lo"' in joined
        assert '"type": "usage"' in joined
        assert '"input_tokens": 5' in joined
        assert chunks[-1] == "data: [DONE]\n\n"

    def test_stream_surfaces_readable_error(self, monkeypatch):
        class _BoomClient(_FakeClient):
            def converse_stream(self, **kw):
                raise RuntimeError("ValidationException: bad modelId")
        _install_fake_boto3(monkeypatch, _BoomClient)
        chunks = _collect(ba.stream("bedrock://us-east-1", "bad", [{"role": "user", "content": "hi"}]))
        joined = "".join(chunks)
        assert "event: error" in joined
        assert "Bedrock rejected the request" in joined


# ── Missing boto3 ────────────────────────────────────────────────────────────

class TestOptionalDependency:
    def test_boto3_missing_raises_with_hint(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "boto3", None)  # forces ImportError on import
        with pytest.raises(ba.BedrockUnavailable):
            ba._boto3()

    def test_friendly_error_for_missing_boto3(self):
        msg = ba.friendly_error(ba.BedrockUnavailable(ba._INSTALL_HINT))
        assert "boto3" in msg
        assert "requirements-optional.txt" in msg

    def test_friendly_error_maps_no_credentials(self):
        class NoCredentialsError(Exception):
            pass
        assert "credentials not found" in ba.friendly_error(NoCredentialsError("Unable to locate credentials")).lower()
