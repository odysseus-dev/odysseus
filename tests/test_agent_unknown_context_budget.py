"""An unknown context window must not be budgeted like a tiny local server (#6678).

``budget_context_for_model`` returns 0 when the endpoint does not prove its
window, and ``compute_input_token_budget`` answers 0 with the conservative
default budget. The agent loop's small-server clamp (``<= 8192``) also caught
0 and cut the request to 1200 tokens, so an open document or earlier context
of a few thousand tokens was trimmed away before the model saw it.
"""
import inspect
import json

import pytest

DONE = 'data: [DONE]\n\n'
MARKER = 'NIGHTJAR-7'
# ~3000 estimated tokens: over the old 1200 clamp, under the 6000 default.
CONTEXT = ''.join(
    f'## Project-{n}\nOwner: team {n}. Status: active. Notes: routine maintenance '
    'and quarterly review scheduled for every workstream in this registry.\n\n'
    for n in range(60)
) + f'## Project-Alpha\nAlias: {MARKER}\n'


def _event(payload):
    return 'data: ' + json.dumps(payload) + '\n\n'


async def _first_request(monkeypatch, tmp_path, context_window):
    import src.agent_loop as al
    from tests.test_agent_runtime_context import _patch_fake_skills
    _patch_fake_skills(monkeypatch)
    monkeypatch.setattr('src.tool_index.get_tool_index', lambda: None)
    monkeypatch.setattr('src.model_context.budget_context_for_model',
                        lambda *args, **kwargs: context_window)
    seen = []

    async def provider(candidates, messages, **kwargs):
        # Like llm_core: the per-candidate factory shapes and trims the request.
        url, model, headers = candidates[0][:3]
        request = kwargs['candidate_request_factory'](0, url, model, headers)
        if inspect.isawaitable(request):
            request = await request
        seen.append((request or {}).get('messages', messages))
        yield _event({'delta': 'ok'})
        yield DONE

    monkeypatch.setattr(al, 'stream_llm_with_fallback', provider)
    history = [
        {'role': 'user', 'content': 'Keep this registry for later:\n\n' + CONTEXT},
        {'role': 'assistant', 'content': 'Saved.'},
        {'role': 'user', 'content': 'What is the alias of Project-Alpha?'},
    ]
    async for _ in al.stream_agent_loop('http://127.0.0.1:9/v1', 'model', history,
                                        max_rounds=1, workspace=str(tmp_path)):
        pass
    assert seen, 'the provider was never called'
    return json.dumps(seen[0])


@pytest.mark.asyncio
async def test_unknown_context_window_keeps_earlier_context(monkeypatch, tmp_path):
    request = await _first_request(monkeypatch, tmp_path, 0)
    assert MARKER in request


@pytest.mark.asyncio
async def test_known_small_window_is_still_clamped(monkeypatch, tmp_path):
    # The clamp exists for small local servers whose window is known (n_ctx
    # 2048 here); it must keep protecting them.
    request = await _first_request(monkeypatch, tmp_path, 2048)
    assert MARKER not in request
