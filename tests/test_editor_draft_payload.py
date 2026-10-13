import sys
import types
from unittest.mock import MagicMock

import pytest
from fastapi import HTTPException


def _load_module(monkeypatch):
    db_stub = types.ModuleType("core.database")
    db_stub.EditorDraft = MagicMock()
    db_stub.SessionLocal = MagicMock()
    monkeypatch.setitem(sys.modules, "core.database", db_stub)
    monkeypatch.delitem(sys.modules, "routes.editor_draft_routes", raising=False)

    import routes.editor_draft_routes as mod

    return mod


def test_load_payload_rejects_non_object_json(monkeypatch):
    mod = _load_module(monkeypatch)

    assert mod._load_payload("[]") == {}
    assert mod._load_payload('"draft"') == {}
    assert mod._load_payload("{bad json") == {}
    assert mod._load_payload('{"layers": []}') == {"layers": []}


def test_dump_payload_enforces_configured_byte_limit(monkeypatch):
    mod = _load_module(monkeypatch)
    monkeypatch.setattr(mod, "EDITOR_DRAFT_MAX_BYTES", 32)

    assert mod._dump_payload({"layers": []}) == '{"layers":[]}'
    with pytest.raises(HTTPException) as exc:
        mod._dump_payload({"pixels": "x" * 40})
    assert exc.value.status_code == 413
