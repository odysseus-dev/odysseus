import pytest

from src.clean_agent_preview import compact_schemas, provider_compatible_tool_choice_request
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS


@pytest.mark.parametrize('model', ['Ajax', 'local/Ajax', 'ajax-test'])
@pytest.mark.parametrize('choice', ['required', {'type': 'function', 'function': {'name': 'manage_tasks'}}])
def test_ajax_auto_decoding_preserves_selected_tool_boundary(model, choice):
    from copy import deepcopy
    tools = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] in {'manage_tasks', 'manage_notes'}]
    request = {'tools': tools, 'tool_choice': choice}
    original = deepcopy(request)
    compatible = provider_compatible_tool_choice_request(request, model)
    assert compatible['tool_choice'] == 'auto'
    names = {s['function']['name'] for s in compatible['tools']}
    assert names == ({'manage_tasks'} if isinstance(choice, dict) else {'manage_tasks', 'manage_notes'})
    assert request == original


def test_ajax_explicit_no_tools_is_preserved():
    request = {'tool_choice': 'none', 'tools': []}
    assert provider_compatible_tool_choice_request(request, 'Ajax') is request


@pytest.mark.parametrize('model', ['Ajax', 'ajax_c375', 'local/Ajax', 'ajax-test'])
def test_ajax_does_not_offer_ask_user(model):
    tools = [s for s in FUNCTION_TOOL_SCHEMAS
             if s['function']['name'] in {'ask_user', 'web_fetch'}]
    names = {s['function']['name'] for s in compact_schemas(tools, model=model)}
    assert names == {'web_fetch'}
    assert any(s['function']['name'] == 'ask_user' for s in tools)


@pytest.mark.parametrize('model', [None, 'kimi-k3', 'odysseus-qwen3.5', 'not-ajax'])
def test_other_models_keep_ask_user(model):
    tools = [s for s in FUNCTION_TOOL_SCHEMAS if s['function']['name'] == 'ask_user']
    assert [s['function']['name'] for s in compact_schemas(tools, model=model)] == ['ask_user']
