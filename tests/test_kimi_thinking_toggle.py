import pytest

from src.model_profiles import supports_user_thinking_toggle
from src.llm_core import _apply_hosted_thinking_mode


@pytest.mark.parametrize('model', ['kimi-k3', 'moonshotai/kimi-k3', 'kimi-k2.5', 'kimi-k2.6'])
@pytest.mark.parametrize('mode', ['on', 'off'])
def test_kimi_toggle_matches_hosted_request(model, mode):
    assert supports_user_thinking_toggle(model)
    payload = {}
    _apply_hosted_thinking_mode(payload, 'moonshot', model, mode)
    assert payload['thinking']['type'] == ('enabled' if mode == 'on' else 'disabled')


def test_older_kimi_is_not_assumed_switchable():
    assert not supports_user_thinking_toggle('kimi-k2-thinking')
