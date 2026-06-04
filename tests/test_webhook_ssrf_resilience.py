import sys
import json
from datetime import datetime

# conftest.py stubs src.database with a fake module; webhook_manager imports
# from it, so drop the stub here to load the real module under test. We RESTORE
# both the sys.modules entry AND the parent `src` package attribute afterwards,
# so the real src.database never leaks into sibling test modules (e.g.
# llm_core.list_model_ids resolves `from src.database import ...` against
# sys.modules at call time, and `import src.database as X` resolves through the
# parent attribute). This mirrors the routes.session_routes isolation fix.
_ABSENT = object()


def _save_module_and_parent_attr(dotted_name):
    """Capture a module's sys.modules entry *and* its parent-package attribute.

    Returns a (module, attr) pair to hand back to
    _restore_module_and_parent_attr. Either may be _ABSENT when not present.
    """
    saved_module = sys.modules.get(dotted_name, _ABSENT)
    pkg_name, _, attr = dotted_name.rpartition(".")
    pkg = sys.modules.get(pkg_name)
    saved_attr = getattr(pkg, attr, _ABSENT) if pkg is not None else _ABSENT
    return saved_module, saved_attr


def _restore_module_and_parent_attr(dotted_name, saved_module, saved_attr):
    """Restore (or remove) both the sys.modules entry and the parent attribute.

    Passing _ABSENT for both clears the cache, which is how we drop the stub
    before the real import below.
    """
    if saved_module is _ABSENT:
        sys.modules.pop(dotted_name, None)
    else:
        sys.modules[dotted_name] = saved_module
    pkg_name, _, attr = dotted_name.rpartition(".")
    pkg = sys.modules.get(pkg_name)
    if pkg is None:
        return
    if saved_attr is _ABSENT:
        if hasattr(pkg, attr):
            delattr(pkg, attr)
    else:
        setattr(pkg, attr, saved_attr)


# Capture the stub state, then clear both bindings so webhook_manager's import
# below produces/binds the real src.database with no stale stub behind it.
_src_database_saved = _save_module_and_parent_attr("src.database")
_restore_module_and_parent_attr("src.database", _ABSENT, _ABSENT)
_core_database = sys.modules.get("core.database")
_core_database_all = getattr(_core_database, "__all__", None) if _core_database is not None else None
if (
    _core_database is not None
    and (
        not getattr(_core_database, "__file__", None)
        or (
            _core_database_all is not None
            and (
                not isinstance(_core_database_all, (list, tuple, set))
                or not all(isinstance(name, str) for name in _core_database_all)
            )
        )
    )
):
    del sys.modules["core.database"]

import pytest
from src.webhook_manager import validate_webhook_url

# webhook_manager is now bound to the real src.database, so restore both the
# sys.modules entry and the parent `src.database` attribute to their original
# stub state to avoid polluting sibling test modules.
_restore_module_and_parent_attr("src.database", *_src_database_saved)


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

    class _Client:
        def __init__(self):
            self.content = ""

        async def post(self, _url, content, headers):
            self.content = content
            assert headers["X-Odysseus-Event"] == "webhook.test"
            return _Response()

    db = _Db()
    client = _Client()
    monkeypatch.setattr(wm, "SessionLocal", lambda: db)

    manager = wm.WebhookManager()
    await manager._client.aclose()
    manager._client = client

    await manager._deliver("hook-1", "http://93.184.216.34/", None, "webhook.test", {"ok": True})

    body = json.loads(client.content)
    payload_timestamp = datetime.fromisoformat(body["timestamp"])
    assert payload_timestamp.tzinfo is None
    assert db.updates[0]["last_triggered_at"].tzinfo is None
    assert db.updates[0]["last_status_code"] == 204
    assert db.committed is True
    assert db.closed is True
