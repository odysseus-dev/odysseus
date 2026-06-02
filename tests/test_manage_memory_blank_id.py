"""Regression: manage_memory edit/delete must reject a blank memory_id.

`m.get("id","").startswith("")` is always True, so a blank id used to match
(and edit/delete) the FIRST stored memory.
"""
import asyncio

from src.ai_interaction import do_manage_memory, set_memory_manager


class _FakeMgr:
    def __init__(self, mems):
        self.mems = mems
        self.saved = None

    def load_all(self):
        return self.mems

    def save(self, mems):
        self.saved = mems


def test_edit_with_blank_memory_id_is_rejected():
    mems = [
        {"id": "aaa111", "text": "first", "owner": None},
        {"id": "bbb222", "text": "second", "owner": None},
    ]
    mgr = _FakeMgr(mems)
    set_memory_manager(mgr)
    result = asyncio.run(do_manage_memory("edit\n\nreplacement text"))
    assert "error" in result and "memory_id" in result["error"], result
    assert mems[0]["text"] == "first"   # first memory untouched
    assert mgr.saved is None            # nothing persisted


def test_delete_with_blank_memory_id_is_rejected():
    mems = [{"id": "aaa111", "text": "first", "owner": None}]
    mgr = _FakeMgr(mems)
    set_memory_manager(mgr)
    result = asyncio.run(do_manage_memory("delete\n\nignored"))
    assert "error" in result and "memory_id" in result["error"], result
    assert len(mems) == 1               # nothing deleted
