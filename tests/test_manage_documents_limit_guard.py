"""manage_documents read parses an LLM-supplied `limit` with int(); a non-numeric
value must fall back to the default rather than raise an uncaught
TypeError/ValueError that crashes the agent turn.
"""
import asyncio
import os
import sys
import tempfile
from pathlib import Path
from unittest.mock import MagicMock

_TMP = Path(tempfile.mkdtemp(prefix="odysseus-doc-limit-"))
os.environ.setdefault("DATA_DIR", str(_TMP))
os.environ.setdefault("DATABASE_URL", f"sqlite:///{_TMP / 'app.db'}")

PROJECT_ROOT = Path(__file__).resolve().parent.parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def test_manage_documents_read_tolerates_non_numeric_limit(monkeypatch):
    import core.database as core_db
    import src.agent_tools.document_tools as dt
    from src.agent_tools.document_tools import ManageDocumentTool

    # mock the DB session (only .close() is used once _get_owned_document is mocked)
    monkeypatch.setattr(core_db, "SessionLocal", lambda: MagicMock())
    # a document exists with content, so execution reaches the limit parse
    fake_doc = MagicMock()
    fake_doc.current_content = "hello world, this is the document body"
    monkeypatch.setattr(dt, "_get_owned_document", lambda *a, **kw: fake_doc)

    res = asyncio.run(ManageDocumentTool().execute(
        '{"action":"read","document_id":"d1","limit":"not-a-number"}',
        {"owner": "alice"},
    ))

    assert isinstance(res, dict), res
    assert res.get("exit_code") != 1, res            # did not error out
    assert "hello world" in str(res), res            # content surfaced (fell back to default)
