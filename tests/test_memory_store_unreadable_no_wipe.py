"""A memory store that cannot be READ must never be overwritten (issue #5673).

`MemoryManager.save` is atomic, and the add/import/extract paths are all
read-modify-write: load the whole store, append, save it back. `load_all`
used to answer a *failed read* with `[]` — indistinguishable from "no
memories" — so a failed read turned into

    load_all() -> []  ->  [].append(new)  ->  save([new])

which atomically replaced the entire store with one entry.

The trigger that actually bites is a store that is **readable but not
parseable** — a truncated file, or one holding `{}` instead of `[]`. Nothing
obstructs the write, so the request succeeds with HTTP 200 and every existing
memory is destroyed silently. Truncation is reachable: `core/database.py`
rewrites memory.json during migration with a plain `open(..., "w")` +
`json.dump`, which is not atomic.

A live exclusive lock is NOT the dangerous case: it blocks the read and the
`os.replace` alike, so the save fails too and the store survives (verified
end-to-end — clean dev returns 500 there and loses nothing).

`load_all_for_update` is the strict loader those callers now use: it raises
`MemoryStoreUnreadable` rather than reporting an empty store.
"""

import builtins
import json
import os

import pytest

from src.memory import MemoryManager, MemoryStoreUnreadable

_SEED = [
    {"id": "m1", "text": "user prefers dark mode", "owner": "alice"},
    {"id": "m2", "text": "user lives in Berlin", "owner": "alice"},
    {"id": "m3", "text": "bob's cat is called Mila", "owner": "bob"},
]


def _seeded(tmp_path):
    m = MemoryManager(str(tmp_path))
    m.save([dict(e) for e in _SEED])
    return m


def _break_reads_of(monkeypatch, target, exc):
    """Make open() raise `exc` for `target` only, leaving every other path alone."""
    real_open = builtins.open

    def fake_open(file, mode="r", *args, **kwargs):
        if os.path.abspath(str(file)) == os.path.abspath(target) and "r" in mode:
            raise exc
        return real_open(file, mode, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", fake_open)


# ── the strict loader signals, rather than reporting "empty" ──────────────

def test_strict_load_raises_on_permission_error(tmp_path, monkeypatch):
    m = _seeded(tmp_path)
    _break_reads_of(monkeypatch, m.memory_file, PermissionError(13, "locked"))
    with pytest.raises(MemoryStoreUnreadable):
        m.load_all_for_update()


def test_strict_load_raises_on_corrupt_json(tmp_path):
    m = _seeded(tmp_path)
    with open(m.memory_file, "w", encoding="utf-8") as f:
        f.write('[{"id": "m1", "text": "truncated mid-writ')
    with pytest.raises(MemoryStoreUnreadable):
        m.load_all_for_update()


def test_strict_load_raises_when_store_is_not_a_list(tmp_path):
    # A file holding `{}` or `null` is not an empty store, it is a broken one.
    m = _seeded(tmp_path)
    with open(m.memory_file, "w", encoding="utf-8") as f:
        json.dump({}, f)
    with pytest.raises(MemoryStoreUnreadable):
        m.load_all_for_update()


def test_strict_load_returns_entries_when_healthy(tmp_path):
    m = _seeded(tmp_path)
    assert {e["id"] for e in m.load_all_for_update()} == {"m1", "m2", "m3"}


def test_strict_load_returns_empty_when_file_genuinely_absent(tmp_path):
    m = _seeded(tmp_path)
    os.remove(m.memory_file)
    # Absent is the one case that legitimately means "no memories yet".
    assert m.load_all_for_update() == []


# ── read paths stay lenient, so an unreadable store can't break chat ──────

def test_read_path_still_degrades_to_empty(tmp_path, monkeypatch):
    m = _seeded(tmp_path)
    _break_reads_of(monkeypatch, m.memory_file, PermissionError(13, "locked"))
    # Context injection / search must not raise; they just see nothing.
    assert m.load_all() == []
    assert m.load(owner="alice") == []


# ── the actual #5673 regression: the store survives ───────────────────────

def test_add_cycle_under_transient_read_error_does_not_wipe(tmp_path, monkeypatch):
    """Mirrors routes/memory/memory_routes.py api_add_memory exactly."""
    m = _seeded(tmp_path)
    new_entry = m.add_entry("a brand new fact", owner="alice")

    with monkeypatch.context() as mp:
        _break_reads_of(mp, m.memory_file, PermissionError(13, "locked"))
        with pytest.raises(MemoryStoreUnreadable):
            all_mem = m.load_all_for_update()
            all_mem.append(new_entry)
            m.save(all_mem)

    # Reads work again; every original memory is still there and the file was
    # never replaced by the single new entry.
    assert {e["id"] for e in m.load_all()} == {"m1", "m2", "m3"}


def test_audit_merge_cannot_drop_other_tenants(tmp_path, monkeypatch):
    """The audit path rebuilds the whole file from load_all + one owner's slice.

    Reading [] there would save only the audited owner's entries and destroy
    every other tenant's memories, so it has to fail closed too.
    """
    m = _seeded(tmp_path)
    alice_slice = [e for e in _SEED if e["owner"] == "alice"]

    with monkeypatch.context() as mp:
        _break_reads_of(mp, m.memory_file, PermissionError(13, "locked"))
        with pytest.raises(MemoryStoreUnreadable):
            all_entries = m.load_all_for_update()
            others = [e for e in all_entries if e.get("owner") != "alice"]
            m.save(alice_slice + others)

    assert any(e["id"] == "m3" for e in m.load_all()), "bob's memory was destroyed"


def test_uses_bump_skips_write_when_unreadable(tmp_path, monkeypatch):
    m = _seeded(tmp_path)
    with monkeypatch.context() as mp:
        _break_reads_of(mp, m.memory_file, PermissionError(13, "locked"))
        m.increment_uses(["m1"])  # must not raise, must not write
    assert {e["id"] for e in m.load_all()} == {"m1", "m2", "m3"}


def test_claim_ownerless_skips_write_when_unreadable(tmp_path, monkeypatch):
    m = _seeded(tmp_path)
    with monkeypatch.context() as mp:
        _break_reads_of(mp, m.memory_file, PermissionError(13, "locked"))
        m.claim_ownerless("alice")
    assert {e["id"] for e in m.load_all()} == {"m1", "m2", "m3"}


# ── the legacy memory.txt migration is preserved ──────────────────────────

def test_corrupt_store_still_migrates_from_legacy_txt(tmp_path):
    m = _seeded(tmp_path)
    with open(m.memory_file, "w", encoding="utf-8") as f:
        f.write("{ not json")
    legacy = os.path.join(str(tmp_path), "memory.txt")
    with open(legacy, "w", encoding="utf-8") as f:
        f.write("recovered fact one\nrecovered fact two\n")

    entries = m.load_all_for_update()
    assert [e["text"] for e in entries] == ["recovered fact one", "recovered fact two"]
