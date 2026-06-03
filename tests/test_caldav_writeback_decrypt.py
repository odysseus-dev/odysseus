"""CalDAV write-back must decrypt the stored password before authenticating.

The password is persisted encrypted ("enc:..."). sync_caldav and
test_connection both decrypt it, but writeback_event passed the raw
ciphertext into caldav.DAVClient, so every create/edit/delete write-back
failed auth and silently never reached the server.
"""
import asyncio

import pytest

import src.caldav_writeback as wb
from src.secret_storage import encrypt


def test_writeback_passes_decrypted_password(monkeypatch):
    secret = "s3cr3t-passw0rd"
    enc = encrypt(secret)
    assert enc != secret  # actually encrypted

    monkeypatch.setattr(
        "routes.prefs_routes._load_for_user",
        lambda owner: {"caldav": {"url": "https://dav.example/cal", "username": "u", "password": enc}},
        raising=False,
    )

    captured = {}

    def _fake_blocking(local_cal_id, ev, delete, url, username, password):
        captured["password"] = password
        return {"ok": True}

    monkeypatch.setattr(wb, "_writeback_blocking", _fake_blocking)

    res = asyncio.run(wb.writeback_event("alice", "caldav", "cal-1", {"uid": "x"}))
    assert res.get("ok") is True
    assert captured["password"] == secret  # decrypted, not the enc: blob


def test_non_caldav_calendar_is_skipped(monkeypatch):
    res = asyncio.run(wb.writeback_event("alice", "local", "cal-1", {"uid": "x"}))
    assert res.get("skipped")
