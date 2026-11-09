"""Backup import must dedup memories against the importing user only.

import_data deduped incoming memories against memory_manager.load_all()
(every tenant\'s rows), so a memory whose text matched ANY other user\'s
memory was silently skipped - the importing user lost their own data. The
dedup must be scoped to the caller\'s own memories. The full multi-tenant
store is still saved back.
"""
import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock

import routes.backup_routes as br


class _Req:
    def __init__(self, body):
        self._body = body

    async def json(self):
        return self._body


def _setup(monkeypatch, tmp_path, store, user="alice"):
    monkeypatch.setattr(br, "require_admin", lambda request: None)
    monkeypatch.setattr(br, "get_current_user", lambda request: user)

    from src.memory import MemoryManager
    mem = MemoryManager(str(tmp_path))
    mem.save(store)

    skills = MagicMock()
    skills.load_all.return_value = []
    router = br.setup_backup_routes(mem, MagicMock(), skills)
    endpoint = None
    for r in router.routes:
        if r.path == "/api/import" and "POST" in getattr(r, "methods", set()):
            endpoint = r.endpoint
    assert endpoint is not None
    return endpoint, mem


def test_user_can_import_memory_matching_another_users_text(monkeypatch, tmp_path):
    # bob already has "buy milk"; alice imports her own "Buy Milk".
    endpoint, mem = _setup(monkeypatch, tmp_path, [{"text": "buy milk", "owner": "bob"}])
    body = {"memories": [{"text": "Buy Milk"}]}
    asyncio.run(endpoint(_Req(body)))
    texts_by_owner = {(e.get("owner"), e.get("text")) for e in mem.load_all_for_update()}
    assert ("alice", "Buy Milk") in texts_by_owner  # not dropped as a "duplicate"
    assert ("bob", "buy milk") in texts_by_owner     # other tenant preserved


def test_users_own_duplicate_is_still_skipped(monkeypatch, tmp_path):
    endpoint, mem = _setup(monkeypatch, tmp_path, [{"text": "buy milk", "owner": "alice"}])
    body = {"memories": [{"text": "Buy Milk"}]}
    asyncio.run(endpoint(_Req(body)))
    alice_milk = [e for e in mem.load_all_for_update()
                  if e.get("owner") == "alice" and e.get("text", "").lower() == "buy milk"]
    assert len(alice_milk) == 1  # the real duplicate is still deduped
