"""Concurrent memory changes must commit against fresh, locked store state."""
from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
import subprocess
import sys
import threading

import pytest

from core.atomic_io import store_lock, store_transaction
from src.memory import MemoryManager, MemoryStoreUnreadable

ROOT = Path(__file__).resolve().parents[1]


def seeded(tmp_path):
    manager = MemoryManager(str(tmp_path))
    manager.save([{'id': 'one', 'text': 'original', 'owner': 'alice', 'uses': 0},
                  {'id': 'other', 'text': 'foreign', 'owner': 'bob', 'uses': 0}])
    return manager


def test_transaction_discards_changes_on_exception(tmp_path):
    manager = seeded(tmp_path)
    before = Path(manager.memory_file).read_bytes()
    with pytest.raises(ValueError):
        with manager.transaction() as entries:
            entries[0]['text'] = 'uncommitted'
            raise ValueError('synthetic failure')
    assert Path(manager.memory_file).read_bytes() == before


def test_noop_does_not_rewrite_store(tmp_path, monkeypatch):
    manager = seeded(tmp_path)
    monkeypatch.setattr(manager, 'save', lambda entries: pytest.fail('no-op rewrote store'))
    with manager.transaction() as entries:
        assert len(entries) == 2


def test_threads_and_instances_keep_all_uses(tmp_path):
    manager = seeded(tmp_path)
    barrier = threading.Barrier(3)

    def bump():
        own = MemoryManager(str(tmp_path))
        barrier.wait()
        for _ in range(15):
            own.increment_uses(['one'])

    with ThreadPoolExecutor(max_workers=3) as pool:
        futures = [pool.submit(bump) for _ in range(3)]
        for future in futures:
            future.result(timeout=10)
    assert manager.load_all_for_update()[0]['uses'] == 45
    assert manager.load_all_for_update()[1]['uses'] == 0


def test_processes_keep_all_uses_and_nested_save_finishes(tmp_path):
    manager = seeded(tmp_path)
    script = r'''
import sys
sys.path.insert(0,sys.argv[1])
from src.memory import MemoryManager
m=MemoryManager(sys.argv[2])
for _ in range(15):m.increment_uses(['one'])
with m.transaction() as entries:
 entries[0]['text']='committed'
 m.save(entries)
'''
    children = [subprocess.Popen([sys.executable, '-c', script, str(ROOT), str(tmp_path)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True) for _ in range(2)]
    try:
        for child in children:
            _, err = child.communicate(timeout=15)
            assert child.returncode == 0, err
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
                child.wait()
    entries = manager.load_all_for_update()
    assert entries[0]['uses'] == 30 and entries[0]['text'] == 'committed'
    assert entries[1]['text'] == 'foreign'


def test_decorator_and_context_share_reentrant_store_lock(tmp_path):
    path = tmp_path / 'shared.json'

    @store_transaction(lambda: path)
    def inner():
        with store_lock(path):
            return 'done'

    with store_lock(path):
        assert inner() == 'done'


def test_unreadable_transaction_changes_nothing(tmp_path):
    manager = seeded(tmp_path)
    path = Path(manager.memory_file)
    path.write_text('{broken')
    with pytest.raises(MemoryStoreUnreadable):
        with manager.transaction() as entries:
            entries.append({'id': 'never', 'text': 'never'})
    assert path.read_text() == '{broken'


def test_snapshot_preserves_new_edited_and_concurrently_deleted_records(tmp_path):
    manager = seeded(tmp_path)
    before = manager.load_all_for_update()
    proposed = deepcopy(before)
    proposed[0]['text'] = 'old model rewrite'
    with manager.transaction() as current:
        current[0]['text'] = 'new user edit'
        current[:] = [row for row in current if row['id'] != 'other']
        current.append({'id': 'new', 'text': 'new addition', 'owner': 'alice'})
    result = manager.apply_snapshot(before, proposed)
    rows = {row['id']: row for row in manager.load_all_for_update()}
    assert set(rows) == {'one', 'new'}
    assert rows['one']['text'] == 'new user edit'
    assert rows['new']['text'] == 'new addition'
    assert result['conflicts'] == ['one'] and result['removed'] == result['updated'] == []


def test_snapshot_removes_and_updates_only_unchanged_records(tmp_path):
    manager = seeded(tmp_path)
    before = manager.load_all_for_update()
    proposed = [dict(before[0], text='audited')]
    result = manager.apply_snapshot(before, proposed)
    assert [row['id'] for row in result['removed']] == ['other']
    assert [row['text'] for row in result['updated']] == ['audited']
    assert manager.load_all_for_update() == result['entries'] == [dict(before[0], text='audited')]


def test_snapshot_does_not_overwrite_or_remove_concurrent_use_bump(tmp_path):
    manager = seeded(tmp_path)
    before = manager.load_all_for_update()
    manager.increment_uses(['one'])
    result = manager.apply_snapshot(before, [before[1]])
    assert result['conflicts'] == ['one'] and result['removed'] == []
    assert manager.load_all_for_update()[0]['uses'] == 1


def test_snapshot_unknown_id_is_rejected_without_writing(tmp_path):
    manager = seeded(tmp_path)
    before = manager.load_all_for_update()
    with pytest.raises(ValueError):
        manager.apply_snapshot(before, before + [{'id': 'invented', 'text': 'invented'}])
    assert manager.load_all_for_update() == before


def test_memory_json_keeps_readable_unicode_and_leaves_no_temporary_file(tmp_path):
    manager = MemoryManager(str(tmp_path))
    with manager.transaction() as entries:
        entries.append(manager.add_entry('Türkçe 日本語', owner='alice'))
    assert 'Türkçe 日本語' in Path(manager.memory_file).read_text()
    assert json.loads(Path(manager.memory_file).read_text())[0]['text'] == 'Türkçe 日本語'
    assert list(tmp_path.glob('memory.json.tmp*')) == []


def test_nested_transactions_are_rejected_without_committing_outer_changes(tmp_path):
    manager = seeded(tmp_path)
    before = Path(manager.memory_file).read_bytes()
    with pytest.raises(RuntimeError, match='Nested memory transactions'):
        with manager.transaction() as entries:
            entries[0]['text'] = 'not committed'
            with MemoryManager(str(tmp_path)).transaction():
                pass
    assert Path(manager.memory_file).read_bytes() == before
    with manager.transaction() as entries:
        entries[0]['text'] = 'later transaction works'
    assert manager.load_all_for_update()[0]['text'] == 'later transaction works'


def test_delayed_vector_refresh_uses_current_text_and_cannot_resurrect_deletion(tmp_path):
    manager = seeded(tmp_path)

    class Vector:
        healthy = True
        added = []
        removed = []

        def add(self, mid, text):
            self.added.append((mid, text))

        def remove(self, mid):
            self.removed.append(mid)

    vector = Vector()
    with manager.transaction() as entries:
        entries[0]['text'] = 'most recent user edit'
    manager.sync_vector(vector, ['one'])
    assert vector.added == [('one', 'most recent user edit')]
    with manager.transaction() as entries:
        entries[:] = [e for e in entries if e['id'] != 'one']
    manager.sync_vector(vector, ['one'])
    assert vector.added == [('one', 'most recent user edit')]
    assert vector.removed == ['one', 'one']


def test_snapshot_legacy_missing_timestamp_is_not_a_concurrent_edit(tmp_path, monkeypatch):
    manager = MemoryManager(str(tmp_path))
    Path(manager.memory_file).write_text('[{"id":"legacy","text":"old text"}]')
    monkeypatch.setattr('src.memory.time.time', lambda: 100)
    before = manager.load_all_for_update()
    monkeypatch.setattr('src.memory.time.time', lambda: 200)
    result = manager.apply_snapshot(before, [dict(before[0], text='new text')])
    assert result['conflicts'] == [] and result['updated'][0]['text'] == 'new text'
    assert manager.load_all_for_update()[0]['text'] == 'new text'


def test_legacy_recovery_rechecks_json_after_waiting_for_writer(tmp_path, monkeypatch):
    import src.memory as memory
    from contextlib import contextmanager
    manager = MemoryManager(str(tmp_path))
    Path(manager.memory_file).write_text('{broken')
    (tmp_path / 'memory.txt').write_text('Old legacy fact')
    real_lock = memory.store_lock
    pending = True

    @contextmanager
    def another_writer_finished(path):
        nonlocal pending
        if pending:
            pending = False
            manager.save([manager.add_entry('Fresh recovered fact', owner='alice')])
        with real_lock(path):
            yield

    monkeypatch.setattr(memory, 'store_lock', another_writer_finished)
    rows = manager.load_all_for_update()
    assert [e['text'] for e in rows] == ['Fresh recovered fact']
    assert [e['text'] for e in json.loads(Path(manager.memory_file).read_text())] == ['Fresh recovered fact']


@pytest.mark.skipif(not hasattr(__import__('os'), 'fork'), reason='requires POSIX fork')
def test_child_does_not_inherit_another_threads_locked_rlock(tmp_path):
    import os
    import select
    import signal
    entered, release = threading.Event(), threading.Event()
    path = tmp_path / 'fork.json'

    def parent_writer():
        with store_lock(path):
            entered.set()
            release.wait(10)

    thread = threading.Thread(target=parent_writer)
    thread.start()
    assert entered.wait(3)
    reader, writer = os.pipe()
    child = os.fork()
    if child == 0:
        try:
            os.close(reader)
            with store_lock(path):
                os.write(writer, b'ok')
        finally:
            os._exit(0)
    os.close(writer)
    release.set()
    thread.join(timeout=3)
    try:
        ready, _, _ = select.select([reader], [], [], 5)
        assert ready and os.read(reader, 2) == b'ok'
    finally:
        os.close(reader)
        if os.waitpid(child, os.WNOHANG)[0] == 0:
            os.kill(child, signal.SIGKILL)
            os.waitpid(child, 0)


def test_valid_existing_store_can_be_read_without_opening_writer_lock(tmp_path, monkeypatch):
    manager = seeded(tmp_path)
    monkeypatch.setattr('src.memory.store_lock', lambda path: pytest.fail('display acquired a writer lock'))
    assert len(MemoryManager(str(tmp_path)).load_all()) == 2


def test_corrupt_store_with_inaccessible_recovery_lock_fails_closed(tmp_path, monkeypatch):
    from contextlib import contextmanager
    manager = seeded(tmp_path)
    Path(manager.memory_file).write_text('{broken')

    @contextmanager
    def denied(path):
        raise PermissionError('owned recovery-lock fixture')
        yield

    monkeypatch.setattr('src.memory.store_lock', denied)
    with pytest.raises(MemoryStoreUnreadable):
        manager.load_all_for_update()
    assert manager.load_all() == []
    assert Path(manager.memory_file).read_text() == '{broken'


def test_vector_failure_does_not_rollback_json_or_skip_other_changed_ids(tmp_path):
    manager = seeded(tmp_path)
    before = Path(manager.memory_file).read_bytes()

    class BrokenVector:
        healthy = True

        def __init__(self):
            self.attempted = set()

        def remove(self, mid):
            pass

        def add(self, mid, text):
            self.attempted.add(mid)
            raise RuntimeError('owned embedding failure')

    vector = BrokenVector()
    manager.sync_vector(vector, ['one', 'other'])
    assert vector.attempted == {'one', 'other'}
    assert Path(manager.memory_file).read_bytes() == before
