import asyncio

from src import llm_core
from src.generation_budget import (
    context_safety_margin,
    estimate_multimodal_image_tokens,
    estimate_tool_schema_tokens,
    fit_output_token_budget,
    parse_context_error,
    plan_context_recovery,
)


def test_large_local_context_budget_keeps_provider_serialization_headroom():
    # vLLM's final chat-template/VL tokenization can exceed the generic text
    # estimate.  The margin must be large enough to avoid an exact-boundary
    # request that fails and has to be retried.
    assert context_safety_margin(32768) >= 1024


def test_output_budget_accounts_for_messages_tools_and_headroom():
    messages = [{"role": "user", "content": "x" * 4000}]
    tools = [{
        "type": "function",
        "function": {
            "name": "write_file",
            "description": "y" * 1000,
            "parameters": {"type": "object", "properties": {}},
        },
    }]

    bounded = fit_output_token_budget(4096, 4096, messages, tools)

    assert estimate_tool_schema_tokens(tools) > 250
    assert 1 <= bounded < 4096


def test_output_budget_reserves_visual_patch_tokens_for_image_blocks():
    messages = [{
        "role": "user",
        "content": [
            {"type": "text", "text": "Please inspect this image."},
            {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
        ],
    }]

    # With no visual reserve, a short text prompt would permit almost the full
    # window for output.  The image reserve keeps room for provider-side VL
    # patch tokenization, which happens after the generic serializer.
    bounded = fit_output_token_budget(32768, 32768, messages)

    assert estimate_multimodal_image_tokens(messages) >= 1024
    assert bounded <= 32768 - 1024 - 256


def test_output_budget_adds_tool_schemas_to_provider_observed_input():
    tools = [{
        "type": "function",
        "function": {
            "name": "inspect_state",
            "description": "x" * 2000,
            "parameters": {"type": "object", "properties": {}},
        },
    }]

    bounded = fit_output_token_budget(
        4096,
        32768,
        [{"role": "user", "content": "ignored when observed tokens exist"}],
        tools,
        observed_input_tokens=28600,
    )

    assert 1 <= bounded < 4096


def test_context_error_parser_reads_openai_compatible_counts():
    details = parse_context_error(
        "This model's maximum context length is 32,768 tokens. "
        "Your request has 30,000 input tokens and requested 8,192 output tokens."
    )

    assert details is not None
    assert details.context_limit == 32768
    assert details.input_tokens == 30000


def test_context_error_parser_reads_parenthesized_vllm_counts():
    details = parse_context_error(
        "Input length (37,184) exceeds model's maximum context length (32,768)."
    )

    assert details is not None
    assert details.context_limit == 32768
    assert details.input_tokens == 37184


def test_context_error_parser_reads_vllm_is_only_limit_wording():
    details = parse_context_error(
        "You passed 30721 input tokens and requested 2048 output tokens. "
        "However, the model's context length is only 32768 tokens, resulting "
        "in a maximum input length of 30720 tokens."
    )

    assert details is not None
    assert details.context_limit == 32768
    assert details.input_tokens == 30721


def test_recovery_allowance_strictly_decreases_from_failed_value():
    plan = plan_context_recovery(
        "maximum context length is 32768; request has 30000 input tokens",
        8192,
        [{"role": "user", "content": "work"}],
    )

    assert plan is not None
    assert 1 <= plan.max_tokens < 8192
    assert plan.max_tokens <= 2768


def test_vllm_input_only_overflow_recovers_when_output_is_provider_default():
    plan = plan_context_recovery(
        "Input length (16,403) exceeds model's maximum context length (16,384).",
        0,
        [{"role": "user", "content": "work"}],
    )

    assert plan is not None
    assert plan.max_tokens == 1024
    assert plan.context_limit == 16384
    assert plan.observed_input_tokens == 16403


def test_vllm_lower_bound_error_uses_conservative_retry_budget():
    message = (
        "This model's maximum context length is 32768 tokens. However, you "
        "requested 7595 output tokens and your prompt contains at least 25174 "
        "input tokens, for a total of at least 32769 tokens."
    )

    details = parse_context_error(message)
    plan = plan_context_recovery(
        message,
        7595,
        [{"role": "user", "content": "x" * 1000}],
    )

    assert details is not None
    assert details.input_tokens_is_lower_bound is True
    assert plan is not None
    assert plan.max_tokens == 1024


def test_non_context_error_has_no_recovery_plan():
    assert plan_context_recovery(
        "provider is temporarily unavailable",
        8192,
        [{"role": "user", "content": "work"}],
    ) is None


def test_fallback_stream_retries_same_candidate_with_smaller_allowance(monkeypatch):
    calls = []

    async def fake_stream(url, model, messages, **kwargs):
        calls.append((model, kwargs["max_tokens"]))
        if len(calls) == 1:
            yield (
                'event: error\ndata: {"status": 400, "text": '
                '"maximum context length is 32768; request has 30000 input tokens"}\n\n'
            )
            return
        yield 'data: {"delta": "recovered"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [("u1", "primary", {}), ("u2", "backup", {})],
                [{"role": "user", "content": "work"}],
                max_tokens=8192,
            )
        ]

    chunks = asyncio.run(run())

    assert [model for model, _ in calls] == ["primary", "primary"]
    assert calls[1][1] < calls[0][1]
    assert any("recovered" in chunk for chunk in chunks)
    assert not any('"type": "fallback"' in chunk for chunk in chunks)


def test_fallback_stream_proactively_caps_output_to_context(monkeypatch):
    calls = []

    async def fake_stream(url, model, messages, **kwargs):
        calls.append(kwargs["max_tokens"])
        yield 'data: {"delta": "ok"}\n\n'
        yield "data: [DONE]\n\n"

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)
    monkeypatch.setattr(llm_core, "get_context_length", lambda url, model: 4096)

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [("u1", "primary", {})],
                [{"role": "user", "content": "x" * 12000}],
                max_tokens=4096,
            )
        ]

    chunks = asyncio.run(run())

    assert calls
    assert 1 <= calls[0] < 4096
    assert any("ok" in chunk for chunk in chunks)


def test_context_recovery_allows_one_conservative_second_trim(monkeypatch):
    calls = []

    async def fake_stream(url, model, messages, **kwargs):
        calls.append((model, kwargs["max_tokens"], list(messages)))
        if len(calls) <= 2:
            observed = 30000 if len(calls) == 1 else 37212
            yield (
                'event: error\ndata: {"status": 400, "text": '
                f'"maximum context length is 32768; request has {observed} input tokens"}}\n\n'
            )
            return
        yield 'data: {"delta": "recovered"}\n\n'
        yield "data: [DONE]\n\n"

    trim_budgets = []
    prune_limits = []

    def fake_trim(messages, context_length, reserve_tokens=0):
        trim_budgets.append((context_length, reserve_tokens))
        return list(messages)

    def fake_prune(messages, *, max_images):
        prune_limits.append(max_images)
        return list(messages)

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)
    monkeypatch.setattr("src.context_compactor.trim_for_context", fake_trim)
    monkeypatch.setattr(
        "src.context_compactor.prune_multimodal_images",
        fake_prune,
        raising=False,
    )

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [("u1", "primary", {})],
                [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "work"},
                        {"type": "image_url", "image_url": {"url": "data:image/png;base64,AA=="}},
                    ],
                }],
                max_tokens=8192,
            )
        ]

    chunks = asyncio.run(run())

    assert len(calls) == 3
    assert len(trim_budgets) == 2
    assert trim_budgets[0][0] <= 32768 - estimate_multimodal_image_tokens(calls[0][2])
    assert trim_budgets[1][0] < trim_budgets[0][0]
    assert prune_limits == [4]
    assert any("recovered" in chunk for chunk in chunks)


def test_context_recovery_retries_transport_wrapper_after_overflow(monkeypatch):
    calls = []
    trim_budgets = []
    prune_limits = []

    async def fake_stream(url, model, messages, **kwargs):
        calls.append((model, kwargs["max_tokens"], list(messages)))
        if len(calls) == 1:
            yield (
                'event: error\ndata: {"status": 400, "text": '
                '"maximum context length is 32768; request has 30000 input tokens"}\n\n'
            )
            return
        if len(calls) == 2:
            yield 'event: error\ndata: {"status": 502, "error": "Upstream protocol error", "fallback_eligible": false}\n\n'
            return
        yield 'data: {"delta": "recovered"}\n\n'
        yield "data: [DONE]\n\n"

    def fake_trim(messages, context_length, reserve_tokens=0):
        trim_budgets.append((context_length, reserve_tokens))
        return list(messages)

    def fake_prune(messages, *, max_images):
        prune_limits.append(max_images)
        return list(messages)

    monkeypatch.setattr(llm_core, "stream_llm", fake_stream)
    monkeypatch.setattr("src.context_compactor.trim_for_context", fake_trim)
    monkeypatch.setattr("src.context_compactor.prune_multimodal_images", fake_prune)

    async def run():
        return [
            chunk
            async for chunk in llm_core.stream_llm_with_fallback(
                [("u1", "primary", {})],
                [{"role": "user", "content": "work"}],
                max_tokens=8192,
            )
        ]

    chunks = asyncio.run(run())

    assert len(calls) == 3
    assert len(trim_budgets) == 2
    assert prune_limits == [0]
    assert trim_budgets[1][0] < trim_budgets[0][0]
    assert any("recovered" in chunk for chunk in chunks)
    assert not any(chunk.startswith("event: error") for chunk in chunks)
