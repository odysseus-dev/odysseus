from types import SimpleNamespace

from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def _note(**overrides):
    data = dict(
        id="n1",
        title="Checklist",
        content="",
        items=None,
        note_type="checklist",
        color=None,
        label=None,
        pinned=False,
        archived=False,
        due_date=None,
        source=None,
        created_at=None,
        updated_at=None,
    )
    data.update(overrides)
    return SimpleNamespace(**data)


def test_serialize_derives_items_from_content_task_lines(monkeypatch):
    # Checklists live as markdown task lines in `content` now; the CLI derives the
    # backward-compat `items` array from them, matching the HTTP/MCP serializer.
    make_core_db_stub(monkeypatch, models=["Note"])
    cli = load_script("odysseus-notes")
    note = _note(content="- [ ] Milk\n- [x] Bread\n  - [ ] Whole wheat")

    assert cli._serialize(note)["items"] == [
        {"text": "Milk", "done": False, "indent": 0},
        {"text": "Bread", "done": True, "indent": 0},
        {"text": "Whole wheat", "done": False, "indent": 1},
    ]


def test_serialize_ignores_legacy_items_column(monkeypatch):
    # The migrated `items` column is no longer the source of truth; a populated
    # column with no task lines in `content` must yield an empty checklist so the
    # CLI doesn't diverge from REST/MCP.
    make_core_db_stub(monkeypatch, models=["Note"])
    cli = load_script("odysseus-notes")
    note = _note(content="plain body, no tasks", items='[{"text": "stale"}]')

    assert cli._serialize(note)["items"] == []


def test_serialize_ignores_fenced_code_task_lines(monkeypatch):
    # Task-looking lines inside a fenced code block are code samples, not items.
    make_core_db_stub(monkeypatch, models=["Note"])
    cli = load_script("odysseus-notes")
    note = _note(content="- [ ] real\n```\n- [ ] sample\n```")

    assert cli._serialize(note)["items"] == [
        {"text": "real", "done": False, "indent": 0},
    ]
