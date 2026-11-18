"""Small-context budgeting regressions; metadata/fake transport only, no inference."""
import asyncio
import json

import httpx
import pytest

from src import llm_core

from src.generation_budget import context_safety_margin, fit_output_token_budget


@pytest.mark.parametrize('context', [512, 2048, 4096])
def test_small_context_preserves_requested_32_with_room(context):
    messages = [{'role': 'user', 'content': 'Reply ready.'}]
    assert fit_output_token_budget(32, context, messages, observed_input_tokens=126) == 32
    assert 126 + 32 + context_safety_margin(context) <= context


@pytest.mark.parametrize('context', [512, 2048, 4096])
def test_insufficient_output_is_explicit_without_changing_request(context):
    details = {}
    requested = 32
    prompt = context - context_safety_margin(context) - 10
    effective = fit_output_token_budget(requested, context, [],
                                       observed_input_tokens=prompt, details=details)
    assert effective == 10
    assert requested == details['requested_max_tokens'] == 32
    assert details['effective_max_tokens'] == effective
    assert details['available_output_tokens'] == 10
    assert 'larger context' in details['warning']
    assert '32' in details['warning'] and '10' in details['warning']


@pytest.mark.parametrize('context', [512, 2048, 4096])
def test_nonstream_reports_real_transport_budget_without_inference(monkeypatch, context):
    payloads = []

    async def transport(request):
        payloads.append(json.loads(request.content))
        return httpx.Response(200, json={'model': 'budget-unit-fixture', 'choices': [
            {'message': {'content': 'unit-fixture-only'}}]})

    async def run():
        async with httpx.AsyncClient(transport=httpx.MockTransport(transport)) as client:
            monkeypatch.setattr(llm_core, '_get_http_client', lambda: client)
            monkeypatch.setattr(llm_core, '_get_cached_response', lambda key: None)
            monkeypatch.setattr(llm_core, 'get_context_length', lambda *args: context)
            details = {}
            reply = await llm_core.llm_call_async('http://127.0.0.1:18101/v1/chat/completions',
                'budget-unit-fixture', [{'role': 'user', 'content': 'Short prompt'}],
                max_tokens=32, generation_budget=details)
            return reply, details

    reply, details = asyncio.run(run())
    assert reply == 'unit-fixture-only'
    assert len(payloads) == 1 and payloads[0]['max_tokens'] == 32
    assert details['requested_max_tokens'] == details['effective_max_tokens'] == 32
    assert details['warning'] is None


def test_no_safe_space_rejects_before_transport(monkeypatch):
    monkeypatch.setattr(llm_core, 'get_context_length', lambda *args: 512)
    monkeypatch.setattr(llm_core, '_get_http_client', lambda: pytest.fail('must not dispatch'))
    details = {}
    with pytest.raises(Exception) as error:
        asyncio.run(llm_core.llm_call_async('http://127.0.0.1:18101/v1/chat/completions',
            'budget-unit-fixture', [{'role': 'user', 'content': 'x' * 5000}],
            max_tokens=32, generation_budget=details))
    assert error.value.status_code == 400
    assert error.value.fallback_eligible is False
    assert error.value.detail['generation_budget']['available_output_tokens'] <= 0
    assert error.value.detail['generation_budget']['effective_max_tokens'] == 0
    assert 'larger context' in error.value.detail['message']


def test_stream_budget_notice_is_not_a_generated_delta(monkeypatch):
    calls = []

    async def fake_stream(url, model, messages, **kwargs):
        calls.append(kwargs['max_tokens'])
        yield 'data: {"delta":"unit-fixture-only"}\n\n'
        yield 'data: [DONE]\n\n'

    monkeypatch.setattr(llm_core, 'stream_llm', fake_stream)
    monkeypatch.setattr(llm_core, 'get_context_length', lambda *args: 512)

    async def run():
        return [chunk async for chunk in llm_core.stream_llm_with_fallback(
            [('fixture-url', 'budget-unit-fixture', {})],
            [{'role': 'user', 'content': 'x' * 1450}], max_tokens=32)]

    chunks = asyncio.run(run())
    notices = [json.loads(c[6:]) for c in chunks if c.startswith('data: {')
               and json.loads(c[6:]).get('type') == 'generation_budget']
    assert len(notices) == 1
    assert notices[0]['requested_max_tokens'] == 32
    assert notices[0]['effective_max_tokens'] == calls[0] < 32
    assert notices[0]['warning'] and 'delta' not in notices[0]


def test_stream_no_space_returns_actionable_error_without_dispatch(monkeypatch):
    async def forbidden_stream(*args, **kwargs):
        pytest.fail('must not dispatch an overflowing prompt')
        yield ''

    monkeypatch.setattr(llm_core, 'stream_llm', forbidden_stream)
    monkeypatch.setattr(llm_core, 'get_context_length', lambda *args: 4096)

    async def run():
        return [chunk async for chunk in llm_core.stream_llm_with_fallback(
            [('fixture-url', 'budget-unit-fixture', {})],
            [{'role': 'user', 'content': 'x' * 12000}], max_tokens=4096)]

    chunks = asyncio.run(run())
    assert len(chunks) == 1 and chunks[0].startswith('event: error')
    error = json.loads(chunks[0].split('data: ', 1)[1])
    assert error['status'] == 400 and error['fallback_eligible'] is False
    assert 'larger context' in error['text']


@pytest.mark.parametrize('context', [512, 2048, 4096])
def test_image_and_tool_reserves_are_not_shrunk_with_text_margin(context):
    from src.generation_budget import estimate_request_tokens
    text = [{'role': 'user', 'content': 'Inspect'}]
    image = [{'role': 'user', 'content': [
        {'type': 'text', 'text': 'Inspect'},
        {'type': 'image_url', 'image_url': {'url': 'data:image/png;base64,AA=='}}]}]
    tools = [{'type': 'function', 'function': {'name': 'fixture', 'description': 'x' * 2000}}]
    details = {}
    fit_output_token_budget(32, context, image, tools, details=details)
    assert estimate_request_tokens(image) >= estimate_request_tokens(text) + 1024
    assert details['estimated_input_tokens'] == estimate_request_tokens(image, tools)
    assert details['available_output_tokens'] == context - details['estimated_input_tokens'] - context_safety_margin(context)
