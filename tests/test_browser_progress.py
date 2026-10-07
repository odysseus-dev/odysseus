import json

from src.clean_agent_preview import BrowserProgress, browser_observation_state


def observation(text='heading "Building sets"', url='https://example.com', click=False):
    value = json.dumps([{'result': {'snapshot': text, 'origin': url,
                                  'lifecycle': {'timer': 123}}}])
    return {'output': ('Done\n\n[post-click page state]\n' if click else '') + value}


def test_repeated_noop_gets_guidance_without_disabling_browser():
    progress = BrowserProgress()
    assert not progress.observe({'action': 'open'}, observation())
    action = {'action': 'click', 'target': '@e108'}
    assert not progress.observe(action, observation(click=True))
    assert 'remains available' in progress.observe(action, observation(click=True))
    assert not progress.observe(action, observation(click=True))


def test_repeat_click_that_changes_page_is_not_a_stall():
    progress = BrowserProgress()
    action = {'action': 'click', 'target': '@e10'}
    for count in range(8):
        assert not progress.observe(action, observation(f'Cart count {count}', click=True))


def test_navigation_and_unknown_observation_reset_stall():
    progress = BrowserProgress()
    action = {'action': 'click', 'target': '@e10'}
    progress.observe(action, observation())
    progress.observe(action, observation())
    assert not progress.observe(action, observation(url='https://example.com/new'))
    assert not progress.observe(action, {'output': 'Done'})
    assert not progress.observe(action, observation())


def test_refs_only_changes_are_not_progress_and_truncation_is_unknown():
    assert browser_observation_state(observation('button [ref=e1]')) == browser_observation_state(observation('button [ref=e52]'))
    assert browser_observation_state({'output': '[{"result": [truncated]'}) is None


def test_waits_and_observations_are_not_flagged_as_failed_actions():
    progress = BrowserProgress()
    for action in ['snapshot', 'wait', 'read', 'find'] * 3:
        assert not progress.observe({'action': action}, observation())
