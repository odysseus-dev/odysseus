import pytest

from routes.chat_routes import _clean_v3_route_for_model
from src.clean_agent_preview import (
    INTERACTIVE_CORE_TOOLS, PREVIEW_TOOLS, evaluate_preview_call,
    scope_preview_contract,
)
from src.tool_policy import ToolPolicy
from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
from src.turn_contract import resolve_full_inventory_contract, resolve_turn_contract


@pytest.mark.parametrize('model', ['ajax', 'deepseek-v4-flash'])
@pytest.mark.parametrize('denied', [frozenset(), frozenset({'python', 'web_search'})])
def test_webui_compact_inventory_survives_both_contract_stages(model, denied):
    assert _clean_v3_route_for_model(model, 'odysseus_compact')
    policy = ToolPolicy(disabled_tools=denied)
    schemas = [s for s in FUNCTION_TOOL_SCHEMAS
               if s['function']['name'] in PREVIEW_TOOLS]
    routed = resolve_turn_contract(capabilities={'notes'}, schemas=schemas,
                                   policy=policy, selected_tools={'manage_notes'})
    preview = resolve_full_inventory_contract(schemas=schemas, policy=policy)
    final = scope_preview_contract(preview, routed, {'notes'},
                                   extra_tools=INTERACTIVE_CORE_TOOLS)
    expected = {'bash', 'python', 'read_file', 'web_search', 'web_fetch', 'ask_user'}
    assert expected - denied <= final.offered
    assert not denied & final.offered
    assert not {'private_browser', 'manage_memory'} & final.offered
    assert {s['function']['name'] for s in final.schemas()} == final.offered


def test_compact_core_calls_pass_execution_guard_when_enabled():
    assert evaluate_preview_call('python', {'code': 'print(1+1)'},
                                 allow_execute_code=True).allowed
    assert not evaluate_preview_call('python', {'code': 'print(1+1)'},
                                     allow_execute_code=False).allowed
    assert evaluate_preview_call('read_file', {'path': '/workspace/a.txt'}).allowed
