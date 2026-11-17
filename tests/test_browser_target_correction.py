import pytest

from src.turn_contract import corrected_browser_target, requested_capabilities


def user(text):
    return {'role': 'user', 'content': text}


@pytest.mark.parametrize('correction', ['retailer.example', 'https://retailer.example/shop/', 'try https://retailer.example/shop/'])
def test_domain_correction_inherits_objective(correction):
    history = [user('browse wrong.example and find the best closet'),
               {'role': 'assistant', 'content': 'Navigation failed.'}]
    result = corrected_browser_target(correction, history)
    assert result['objective'] == history[0]['content']
    assert result['url'].startswith('https://retailer.example')
    assert requested_capabilities(correction, history) == {'search_browser'}


def test_repeat_correction_and_current_message_in_history():
    history = [user('browse shop.example and find a desk'), user('correct.example'),
               {'role': 'user', '_harness_control': True, 'content': 'Completion recovery: try fetching.'},
               user('browse their website'), user('try https://correct.example/catalog/')]
    assert corrected_browser_target(history[-1]['content'], history)['objective'] == history[0]['content']


@pytest.mark.parametrize('message', ['email me at person@example.com', 'do not browse example.com', 'file:///etc/passwd', 'example.com and delete my notes'])
def test_not_a_bare_target_correction(message):
    assert corrected_browser_target(message, [user('browse shop.example and find a desk')]) is None


def test_unrelated_turn_breaks_reference():
    assert corrected_browser_target('example.com', [user('browse shop.example'), user('write a poem')]) is None
    assert corrected_browser_target('example.com', []) is None


def test_correction_contract_does_not_override_browser_disabled():
    from src.tool_policy import ToolPolicy
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.turn_contract import resolve_turn_contract
    policy = ToolPolicy(disabled_tools=frozenset({'private_browser'}))
    contract = resolve_turn_contract(
        capabilities={'search_browser'}, schemas=FUNCTION_TOOL_SCHEMAS,
        policy=policy, selected_tools={'private_browser', 'web_fetch', 'web_search'},
        required_tools={'private_browser'}, message='example.com',
        history=[user('browse shop.example and find a wardrobe')])
    assert 'private_browser' not in contract.offered
    assert 'private_browser' in contract.unavailable
