"""Exercise the actual audit/consolidation entry points while their LLM is pending."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import importlib.util
import json
from pathlib import Path

import pytest

from src.memory import MemoryManager


def extractor():
    path = Path(__file__).resolve().parents[1] / 'services/memory/memory_extractor.py'
    spec = importlib.util.spec_from_file_location('memory_audit_concurrency', path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def manager_fixture(tmp_path):
    manager = MemoryManager(str(tmp_path))
    manager.save([
        dict(manager.add_entry(text, owner=owner), id=mid)
        for mid, text, owner in [
            ('edit', 'User builds telescopes', 'alice'),
            ('delete', 'User keeps paper maps', 'alice'),
            ('uses', 'User restores antique radios', 'alice'),
            ('clean', 'User lives in a coastal village', 'alice'),
            ('drop', 'Obsolete synthetic note', 'alice'),
            ('foreign', 'Private foreign record', 'bob'),
        ]
    ])
    return manager


def concurrent_changes(manager):
    with manager.transaction() as entries:
        next(e for e in entries if e['id'] == 'edit')['text'] = 'New user telescope edit'
        entries[:] = [e for e in entries if e['id'] != 'delete']
        entries.append(dict(manager.add_entry('New concurrent owner record', owner='alice'), id='new'))
    manager.increment_uses(['uses'])


class Vector:
    healthy = True

    def __init__(self, manager):
        self.manager = manager
        self.added = {}
        self.removed = []

    def add(self, mid, text):
        # A separate writer must be able to commit even while embedding runs.
        with ThreadPoolExecutor(max_workers=1) as pool:
            pool.submit(self.manager.increment_uses, ['foreign']).result(timeout=3)
        self.added[mid] = text

    def remove(self, mid):
        self.removed.append(mid)

    def rebuild(self, entries):
        pytest.fail('audit must not wipe/rebuild the shared vector collection')


@pytest.mark.asyncio
@pytest.mark.parametrize('owner', ['alice', None])
async def test_audit_preserves_concurrent_changes_and_syncs_only_committed_ids(tmp_path, monkeypatch, owner):
    from src import llm_core
    manager = manager_fixture(tmp_path)
    vector = Vector(manager)
    module = extractor()
    calls = []

    async def llm(*args, **kwargs):
        payload = json.loads(args[2][1]['content'])
        calls.append(payload)
        concurrent_changes(manager)
        return json.dumps([
            dict(e, text='LLM rewrite of ' + e['id']) for e in payload
            if e['id'] != 'drop'
        ])

    monkeypatch.setattr(llm_core, 'llm_call_async', llm)
    result = await module.audit_memories(manager, vector, 'http://fixture/v1', 'fixture', owner=owner)
    rows = {e['id']: e for e in manager.load_all_for_update()}
    assert 'error' not in result
    assert set(rows) == {'edit', 'uses', 'clean', 'new', 'foreign'}
    assert rows['edit']['text'] == 'New user telescope edit'
    assert rows['uses']['uses'] == 1 and rows['uses']['text'] == 'User restores antique radios'
    assert rows['new']['text'] == 'New concurrent owner record'
    assert rows['clean']['text'] == 'LLM rewrite of clean'
    if owner:
        assert rows['foreign']['text'] == 'Private foreign record'
    assert set(vector.removed) == ({'drop', 'clean'} if owner else {'drop', 'clean', 'foreign'})
    assert set(vector.added) == ({'clean'} if owner else {'clean', 'foreign'})
    assert result['after'] == (4 if owner else 5)
    assert not Path(module._tidy_state_path(manager)).exists(), 'concurrent additions were never audited'


@pytest.mark.asyncio
async def test_successful_no_conflict_audit_records_fingerprint_and_skips_next_llm(tmp_path, monkeypatch):
    from src import llm_core
    manager = manager_fixture(tmp_path)
    module = extractor()
    calls = []

    async def llm(*args, **kwargs):
        calls.append(1)
        return args[2][1]['content']

    monkeypatch.setattr(llm_core, 'llm_call_async', llm)
    assert (await module.audit_memories(manager, None, 'http://fixture/v1', 'fixture', owner='alice'))['after'] == 5
    assert (await module.audit_memories(manager, None, 'http://fixture/v1', 'fixture', owner='alice'))['already_tidy']
    assert calls == [1]


@pytest.mark.asyncio
async def test_scheduled_consolidation_preserves_edits_new_rows_deletions_and_use_counts(tmp_path, monkeypatch):
    from src import ai_interaction, builtin_actions, constants, llm_core, task_endpoint
    manager = manager_fixture(tmp_path)
    monkeypatch.setattr(constants, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(ai_interaction, '_memory_vector', None)
    monkeypatch.setattr(task_endpoint, 'resolve_task_candidates', lambda **kwargs: [('http://fixture/v1', 'fixture', {})])

    async def quiet(*args):
        pass

    monkeypatch.setattr(builtin_actions, 'wait_for_interactive_quiet', quiet)

    async def llm(*args, **kwargs):
        concurrent_changes(manager)
        return json.dumps({'keep': [{'id': mid, 'text': 'LLM rewrite of ' + mid} for mid in ['edit', 'delete', 'clean']],
                           'drop': [{'id': 'uses', 'reason': 'obsolete'}, {'id': 'drop', 'reason': 'obsolete'}]})

    monkeypatch.setattr(llm_core, 'llm_call_async_with_fallback', llm)
    message, ok = await builtin_actions.action_consolidate_memory('alice')
    assert ok, message
    rows = {e['id']: e for e in manager.load_all_for_update()}
    assert set(rows) == {'edit', 'uses', 'clean', 'foreign', 'new'}
    assert rows['edit']['text'] == 'New user telescope edit'
    assert rows['uses']['uses'] == 1
    assert rows['clean']['text'] == 'LLM rewrite of clean'
    assert rows['foreign']['text'] == 'Private foreign record'
    assert 'removed 1, cleaned 1' in message


@pytest.mark.asyncio
async def test_extraction_reloads_after_slow_vector_search_and_preserves_new_writes(tmp_path, monkeypatch):
    from src import llm_core
    manager = MemoryManager(str(tmp_path))
    manager.save([dict(manager.add_entry('I live in Lisbon', owner='alice'), id='old')])
    module = extractor()

    class Session:
        owner = 'alice'
        session_id = 'fixture'

        def get_context_messages(self):
            return [{'role': 'user', 'content': 'I live in Lisbon.'},
                    {'role': 'assistant', 'content': 'Noted.'}]

    class SlowVector:
        healthy = True

        def find_similar(self, text, threshold):
            def independent_write():
                with MemoryManager(str(tmp_path)).transaction() as rows:
                    rows[0]['text'] = 'User moved to Osaka'
                    rows.append(dict(manager.add_entry('Concurrent record survives', owner='alice'), id='new'))
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(independent_write).result(timeout=3)
            return 'old'  # The embedding matched the old Lisbon text.

        def remove(self, mid):
            pass

        def add(self, mid, text):
            pass

    async def llm(*args, **kwargs):
        return '[{"text":"I live in Lisbon","evidence":"I live in Lisbon"}]'

    monkeypatch.setattr(llm_core, 'llm_call_async', llm)
    await module.extract_and_store(Session(), manager, SlowVector(), 'http://fixture/v1', 'fixture')
    rows = {e['id']: e for e in manager.load_all_for_update()}
    assert rows['old']['text'] == 'User moved to Osaka'
    assert rows['new']['text'] == 'Concurrent record survives'
    assert any(e['text'] == 'I live in Lisbon' for e in rows.values()), 'a stale vector hit suppressed the fact'
