"""Regression tests for py/polynomial-redos sinks in the agent preview parsers.

Each pattern had overlapping quantifiers (adjacent `\\s` runs, or a lazy group
that rescanned the rest of the line from every candidate) that backtrack
polynomially when the tail fails. The rewrites keep the same matches for the
text these tools emit. The old patterns took 5-20s on the flood inputs below;
the new ones finish in milliseconds against a 4s budget.
"""

import time

import pytest

from src.browser_observation import compact_browser_observation
from src.clean_agent_preview import (
    email_search_result_empty,
    is_google_sorry_url,
    skills_terminal_response,
    tasks_terminal_response,
)

_BUDGET_S = 4.0
_DASH = chr(0x2014)  # the separator src/tools/system.py writes into task rows


def _timed(fn, *args):
    start = time.perf_counter()
    result = fn(*args)
    return result, time.perf_counter() - start


# Browser snapshot: bare `generic` container lines are dropped, others kept.

def test_generic_snapshot_lines_are_dropped():
    snapshot = '- generic [ref=e1]:\n  - generic\n-generic:\n- generic button\n- button "Go" [ref=e2]'
    output = compact_browser_observation({'snapshot': snapshot})
    assert output == '- generic button\n- button "Go" [ref=e2]'


def test_generic_filter_whitespace_flood_is_fast():
    (output, dt) = _timed(compact_browser_observation, {'snapshot': '\t' * 120000 + '!'})
    assert dt < _BUDGET_S, f'generic filter took {dt:.2f}s'
    assert output.endswith('!')


# Browser CLI text: status text is split from the post-interaction state.

@pytest.mark.parametrize('label', ['post-click page state', 'page state after failed click'])
def test_post_interaction_state_is_split(label):
    raw = f'Clicked\n\n[{label}]\n{{"snapshot": "- link \\"A\\" [ref=e3]"}}'
    assert compact_browser_observation(raw) == 'Clicked\n\n- link "A" [ref=e3]'


@pytest.mark.parametrize('opener', ['\n\n[post-', '\n\n[page state after failed '])
def test_post_interaction_split_flood_is_fast(opener):
    (_, dt) = _timed(compact_browser_observation, 'x' + opener * 15000)
    assert dt < _BUDGET_S, f'post-interaction split took {dt:.2f}s'


# Task rows: `N. name (id) <dash> detail`, where the name may contain parentheses.

def test_task_rows_parse():
    raw = (f'1. Backup (nightly) (abc123) {_DASH} active, daily at 02:00\n'
           f'2.  (e2) {_DASH} paused\n'
           f'not a task row')
    assert tasks_terminal_response(raw) == (
        f'Tasks (2):\n- [Backup (nightly)](#task-abc123) {_DASH} active\n'
        f'- [](#task-e2) {_DASH} paused'
    )


@pytest.mark.parametrize('evil', ['0.' + '\t' * 3000 + '!', '0.\t\t(' + '\t((' * 20000])
def test_task_row_flood_is_fast(evil):
    (output, dt) = _timed(tasks_terminal_response, evil)
    assert dt < _BUDGET_S, f'task row parse took {dt:.2f}s'
    assert output == evil.strip()


# Skill inventory: `- name (category): summary` under a status heading.

def test_skill_rows_parse():
    raw = '## Published\n- pdf-tools (documents): Read PDFs\n- web\n## Drafts\n- notes  (writing)'
    assert skills_terminal_response(raw) == (
        'Skills (3):\n\n**Published**\n- [pdf-tools](#skill-pdf-tools) (documents)\n'
        '- [web](#skill-web)\n\n**Drafts**\n- [notes](#skill-notes) (writing)'
    )


def test_nameless_skill_bullet_is_not_a_row():
    raw = '## Published\n-  : x'
    assert skills_terminal_response(raw) == raw


def test_skill_row_flood_is_fast():
    (_, dt) = _timed(skills_terminal_response, '## Published\n- \t9' + '\t\t' * 30000 + '!')
    assert dt < _BUDGET_S, f'skill row parse took {dt:.2f}s'


# Email search: the plain-text "no results" message.

@pytest.mark.parametrize('value,expected', [
    ('No emails matched from:bob.', True),
    ('  no emails matched "invoice"\n\n', True),
    ('No emails matched ', False),
    ('No emails matched x\nmore', False),
    ('Found 2 emails', False),
])
def test_email_no_results_message(value, expected):
    assert email_search_result_empty(value) is expected


def test_email_no_results_flood_is_fast():
    (result, dt) = _timed(email_search_result_empty, 'No emails matched x' + '\t' * 60000 + '\n!')
    assert dt < _BUDGET_S, f'email no-results check took {dt:.2f}s'
    assert result is False


# Google rate-limit interstitial.

@pytest.mark.parametrize('url,expected', [
    ('https://www.google.com/sorry/index?continue=x', True),
    ('http://google.co.uk/sorry/', True),
    ('HTTPS://IPV4.GOOGLE.COM/SORRY/', True),
    ('https://example.com/sorry/?next=https://www.google.de/sorry/', True),
    ('https://example.com/sorry/', False),
    ('https://notgoogle.com/sorry/', False),
    ('https://www.google.com/search?q=sorry', False),
    ('', False),
])
def test_is_google_sorry_url(url, expected):
    assert is_google_sorry_url(url) is expected


@pytest.mark.parametrize('evil', [
    'http://google.' + '..google.' * 50000,
    'http://' + 'x.' * 200000 + 'google./sorry/',  # reaches the host match
])
def test_google_sorry_flood_is_fast(evil):
    (result, dt) = _timed(is_google_sorry_url, evil)
    assert dt < _BUDGET_S, f'google sorry check took {dt:.2f}s'
    assert result is False
