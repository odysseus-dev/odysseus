"""Tests for editor_draft_routes.py — ownership and summary helper functions."""

import os
import sys
import types
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Stub heavy deps before importing the module under test.
if "core.database" not in sys.modules:
    _db = types.ModuleType("core.database")
    _db.EditorDraft = MagicMock()
    _db.SessionLocal = MagicMock()
    sys.modules["core.database"] = _db
if "src.auth_helpers" not in sys.modules:
    _ah = types.ModuleType("src.auth_helpers")
    _ah.get_current_user = MagicMock(return_value=None)
    sys.modules["src.auth_helpers"] = _ah

from routes.editor_draft_routes import _owns, _summary  # noqa: E402


def _draft(**kwargs):
    defaults = {
        "id": "draft-abc",
        "owner": "alice",
        "name": "My Draft",
        "source_image_id": None,
        "width": 800,
        "height": 600,
        "thumbnail": None,
        "created_at": None,
        "updated_at": None,
    }
    defaults.update(kwargs)
    return SimpleNamespace(**defaults)


# ── _owns ──

class TestOwns:
    def test_auth_disabled_always_owns(self):
        d = _draft(owner="alice")
        assert _owns(d, None) is True

    def test_matching_owner(self):
        d = _draft(owner="alice")
        assert _owns(d, "alice") is True

    def test_different_owner_denied(self):
        d = _draft(owner="bob")
        assert _owns(d, "alice") is False

    def test_draft_no_owner_auth_disabled(self):
        d = _draft(owner=None)
        assert _owns(d, None) is True

    def test_draft_no_owner_with_authed_user(self):
        # A draft with no owner belongs to no one when auth is enabled.
        d = _draft(owner=None)
        assert _owns(d, "alice") is False

    def test_empty_string_owner_not_same_as_none(self):
        d = _draft(owner="")
        # owner="" vs user="alice" — should not match
        assert _owns(d, "alice") is False


# ── _summary ──

class TestSummary:
    def test_basic_fields_present(self):
        d = _draft(id="x1", name="Test", width=1024, height=768)
        s = _summary(d)
        assert s["id"] == "x1"
        assert s["name"] == "Test"
        assert s["width"] == 1024
        assert s["height"] == 768

    def test_payload_not_included(self):
        d = _draft()
        s = _summary(d)
        assert "payload" not in s

    def test_none_name_falls_back_to_untitled(self):
        d = _draft(name=None)
        assert _summary(d)["name"] == "Untitled"

    def test_empty_name_falls_back_to_untitled(self):
        d = _draft(name="")
        assert _summary(d)["name"] == "Untitled"

    def test_none_dates_serialized_as_none(self):
        d = _draft(created_at=None, updated_at=None)
        s = _summary(d)
        assert s["created_at"] is None
        assert s["updated_at"] is None

    def test_datetime_serialized_to_iso_string(self):
        ts = datetime(2024, 6, 1, 12, 0, 0)
        d = _draft(created_at=ts, updated_at=ts)
        s = _summary(d)
        assert s["created_at"] == "2024-06-01T12:00:00"
        assert s["updated_at"] == "2024-06-01T12:00:00"

    def test_source_image_id_forwarded(self):
        d = _draft(source_image_id="img-99")
        assert _summary(d)["source_image_id"] == "img-99"

    def test_thumbnail_forwarded(self):
        d = _draft(thumbnail="data:image/png;base64,abc")
        assert _summary(d)["thumbnail"] == "data:image/png;base64,abc"
