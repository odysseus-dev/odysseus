"""routes/note/note_routes.py imports only what it uses, and takes its conflict message from one place."""

import ast
import pathlib

from core.note_writes import NOTE_BODY_CONFLICT

SOURCE = pathlib.Path(__file__).resolve().parents[1] / "routes" / "note" / "note_routes.py"


def _tree():
    return ast.parse(SOURCE.read_text(encoding="utf-8"))


def test_every_imported_name_is_used():
    tree = _tree()
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.col_offset == 0:
            imported |= {alias.asname or alias.name for alias in node.names}
    used = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
    assert not sorted(imported - used), f"imported but never used: {sorted(imported - used)}"


def test_conflict_message_is_written_once():
    # The 409 message is core.note_writes.NOTE_BODY_CONFLICT, shared with the
    # agent tool; the routes never spell out a copy of their own.
    tree = _tree()
    strings = [node.value for node in ast.walk(tree)
               if isinstance(node, ast.Constant) and node.value in ("Note changed elsewhere", NOTE_BODY_CONFLICT)]
    assert strings == []
    assert "NOTE_BODY_CONFLICT" in {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)}
