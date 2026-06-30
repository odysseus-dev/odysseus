import sys
import types
from unittest.mock import MagicMock

from tests.helpers.cli_loader import load_script


def _load_cli(monkeypatch):
    personal_docs = types.ModuleType("src.personal_docs")
    personal_docs.PersonalDocsManager = MagicMock()
    monkeypatch.setitem(sys.modules, "src.personal_docs", personal_docs)
    return load_script("odysseus-personal")


def test_file_rows_skips_invalid_rows(monkeypatch):
    cli = _load_cli(monkeypatch)

    assert cli._file_rows([
        {"name": "notes.txt", "path": "/tmp/notes.txt"},
        "bad-row",
        None,
    ]) == [{"name": "notes.txt", "path": "/tmp/notes.txt"}]
