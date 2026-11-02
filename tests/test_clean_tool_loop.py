import importlib.util
from pathlib import Path

spec = importlib.util.spec_from_file_location('clean_loop_experiment', Path(__file__).parents[1] / 'scripts/test_clean_tool_loop.py')
experiment = importlib.util.module_from_spec(spec)
spec.loader.exec_module(experiment)


def call(name, arguments):
    return {'id': 'test-call', 'type': 'function', 'function': {'name': name, 'arguments': arguments}}


def test_stable_inventory_does_not_depend_on_spelling_or_history():
    assert experiment.inventory('stable', 'mraket', []) == experiment.inventory('stable', 'calendar', [])


def test_web_permission_filters_inventory():
    offered = experiment.inventory('stable', 'search', [], web=False)
    names = {s['function']['name'] for s in offered}
    assert not names & experiment.FAMILY_TOOLS['search_browser']
    assert 'manage_notes' in names


def test_model_query_passes_unchanged():
    class Recorder:
        def execute(self, name, args):
            return args
    assert experiment.validated_execute(call('web_search', '{"query":"stock market today"}'), experiment.SCHEMAS, Recorder()) == {'query': 'stock market today'}


def test_denied_tool_cannot_execute():
    class Fail:
        def execute(self, *args):
            raise AssertionError('must not dispatch')
    assert 'error' in experiment.validated_execute(call('web_search', '{"query":"x"}'), [], Fail())


def test_bad_arguments_return_error_not_repair():
    assert 'error' in experiment.validated_execute(call('manage_notes', '{"action":"made_up"}'), experiment.SCHEMAS, experiment.Sandbox())


def test_mutation_is_not_executed():
    sandbox = experiment.Sandbox()
    assert 'error' in sandbox.execute('manage_notes', {'action': 'delete', 'id': 'note-102'})
    assert len(sandbox.execute('manage_notes', {'action': 'list'})['notes']) == 2


def test_v3_keeps_tested_hints():
    browser = next(s for s in experiment.SCHEMAS if s['function']['name'] == 'private_browser')
    assert browser['function'].get('description')


def test_trained_notes_view_action_returns_record():
    result = experiment.validated_execute(call('manage_notes', '{"action":"view","id":"note-102"}'), experiment.SCHEMAS, experiment.Sandbox())
    assert result['note']['id'] == 'note-102'
