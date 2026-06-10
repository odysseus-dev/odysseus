"""Regression test for _recover_empty_session_model honoring pinned models.

Producer side (routes/model_routes.py): the model-list / endpoint-save
endpoints build the picker dropdown with
    _visible_models(cached_models, hidden_models, pinned_models)
so an admin-pinned model ID (one that does NOT appear in /v1/models, e.g. a
cloud deployment ID) shows up in the dropdown and is selectable.

Consumer side (routes/chat_routes._recover_empty_session_model): when a
session's model was never persisted (Issue #587 window), it tries to recover
the model from the matching endpoint. The bug: it only looks at
`cached_models` (bails with `if not cached: return False`) and calls
`_visible_models(cached, hidden)` WITHOUT the pinned list — so a pinned-only
endpoint can never recover, and the chat POSTs upstream with model="".
"""

import os
import sys
import tempfile
import types
from unittest.mock import MagicMock

# Stub optional deps pulled in by `core` package __init__ but not installed
# in the test venv.
for _m in ["pyotp"]:
    if _m not in sys.modules:
        try:
            __import__(_m)
        except Exception:
            sys.modules[_m] = MagicMock()

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Point the DB at a throwaway sqlite file BEFORE importing core.database.
_DB_PATH = tempfile.mktemp(suffix=".db")
os.environ["DATABASE_URL"] = "sqlite:///" + _DB_PATH


def _setup():
    import core.database as db
    db.init_db()
    return db


def test_recover_empty_session_uses_pinned_only_endpoint():
    db = _setup()
    import routes.chat_routes as cr

    # Endpoint with NO cached models but a single admin-pinned deployment ID
    # — exactly the documented cloud-deploy-ID case the picker supports.
    s = db.SessionLocal()
    try:
        ep = db.ModelEndpoint(
            id="ep-pinned",
            name="Pinned Cloud",
            base_url="https://cloud.example.com/v1",
            is_enabled=True,
            cached_models=None,
            hidden_models=None,
            pinned_models='["my-cloud-deploy-id"]',
            model_type="llm",
        )
        s.add(ep)
        sess_row = db.Session(
            id="sess-1",
            name="t",
            model="",  # never persisted
            endpoint_url="https://cloud.example.com/v1",
        )
        s.add(sess_row)
        s.commit()
    finally:
        s.close()

    class _Sess:
        model = ""
        endpoint_url = "https://cloud.example.com/v1"

    sess = _Sess()
    recovered = cr._recover_empty_session_model(sess, "sess-1", owner=None)

    assert recovered is True, "should recover the pinned model"
    assert sess.model == "my-cloud-deploy-id", f"got {sess.model!r}"
