import os
import sys
import json
from datetime import datetime
from unittest.mock import patch

import pytest

from tests.helpers.import_state import clear_module, preserve_import_state

# conftest.py stubs src.database; drop the stub so webhook_manager imports the
# real module. preserve_import_state restores sys.modules and parent-package
# attributes for both src.database and core.database after the block, preventing
# stub/engine leakage into siblings.
#
# Importing the real core.database runs init_db() -> create_all() against
# DATABASE_URL (default sqlite:///./data/app.db); in a clean worktree with no
# ./data directory that raises sqlite3.OperationalError during collection. Pin
# DATABASE_URL to in-memory SQLite for the import: it needs no filesystem path
# and leaves no artifact, and these tests never touch the real engine
# (validate_webhook_url is pure; the delivery test monkeypatches SessionLocal).
# patch.dict restores the prior DATABASE_URL after the block.
with patch.dict(os.environ, {"DATABASE_URL": "sqlite:///:memory:"}), \
        preserve_import_state("src.database", "core.database"):
    clear_module("src.database")
    _core_database = sys.modules.get("core.database")
    _core_database_all = (
        getattr(_core_database, "__all__", None) if _core_database is not None else None
    )
    if _core_database is not None and (
        not getattr(_core_database, "__file__", None)
        or (
            _core_database_all is not None
            and (
                not isinstance(_core_database_all, (list, tuple, set))
                or not all(isinstance(name, str) for name in _core_database_all)
            )
        )
    ):
        del sys.modules["core.database"]
    from src.webhook_manager import validate_webhook_url


def test_webhook_url_ssrf_mitigation():
    # SSRF bypasses that must be rejected, including IPv6 unspecified and
    # IPv4-mapped IPv6 (loopback + cloud metadata).
    private_urls = [
        "http://[::]/",
        "http://[::ffff:127.0.0.1]/",
        "http://[::ffff:169.254.169.254]/",
        "http://127.0.0.1/",
        "http://0.0.0.0/",
    ]
    for url in private_urls:
        with pytest.raises(ValueError) as exc:
            validate_webhook_url(url)
        assert "private/internal addresses" in str(exc.value)

    # A clearly public IP literal must still be accepted.
    public_url = "http://93.184.216.34/"
    assert validate_webhook_url(public_url) == public_url


@pytest.mark.asyncio
async def test_webhook_delivery_uses_naive_utc_timestamps(monkeypatch):
    import src.webhook_manager as wm

    class _Query:
        def __init__(self, updates):
            self.updates = updates

        def filter(self, *_args, **_kwargs):
            return self

        def update(self, values):
            self.updates.append(values)

    class _Db:
        def __init__(self):
            self.updates = []
            self.committed = False
            self.closed = False

        def query(self, _model):
            return _Query(self.updates)

        def commit(self):
            self.committed = True

        def rollback(self):
            pass

        def close(self):
            self.closed = True

    class _Response:
        status_code = 204

    db = _Db()
    monkeypatch.setattr(wm, "SessionLocal", lambda: db)

    manager = wm.WebhookManager()

    # Replace the pinned-transport send seam so no real socket is opened. The
    # public-IP literal below still exercises _validated_public_ips (which pins
    # the connect); the captured content proves the body/headers are built.
    captured = {}

    async def _fake_send(url, body, headers, ip):
        captured["content"] = body
        captured["ip"] = str(ip)
        assert headers["X-Odysseus-Event"] == "webhook.test"
        return _Response()

    monkeypatch.setattr(manager, "_send_request", _fake_send)

    await manager._deliver("hook-1", "http://93.184.216.34/", None, "webhook.test", {"ok": True})

    # The delivery must have pinned to the literal public IP from the URL.
    assert captured["ip"] == "93.184.216.34"
    body = json.loads(captured["content"])
    payload_timestamp = datetime.fromisoformat(body["timestamp"])
    assert payload_timestamp.tzinfo is None
    assert db.updates[0]["last_triggered_at"].tzinfo is None
    assert db.updates[0]["last_status_code"] == 204
    assert db.committed is True
    assert db.closed is True
