from types import SimpleNamespace

from tests.helpers.cli_loader import load_script
from tests.helpers.db_stubs import make_core_db_stub


def test_serialize_ignores_invalid_note_items(monkeypatch):
    make_core_db_stub(monkeypatch, models=["Note"])
    cli = load_script("odysseus-notes")
    note = SimpleNamespace(
        id="n1",
        title="Checklist",
        content="",
        items="{bad json",
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

    assert cli._serialize(note)["items"] == []


def test_serialize_keeps_list_note_items(monkeypatch):
    make_core_db_stub(monkeypatch, models=["Note"])
    cli = load_script("odysseus-notes")
    note = SimpleNamespace(
        id="n1",
        title="Checklist",
        content="",
        items='[{"text": "done"}]',
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

    assert cli._serialize(note)["items"] == [{"index": 0, "text": "done", "done": False, "indent": 0}]


def test_serialize_skips_invalid_note_item_rows(monkeypatch):
    make_core_db_stub(monkeypatch, models=["Note"])
    cli = load_script("odysseus-notes")
    note = SimpleNamespace(
        id="n1",
        title="Checklist",
        content="",
        items='[{"text": "done"}, "bad", null, 3]',
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

    assert cli._serialize(note)["items"] == [{"index": 0, "text": "done", "done": False, "indent": 0}]


def test_serialize_reads_items_from_content_task_lines(monkeypatch):
    make_core_db_stub(monkeypatch, models=["Note"])
    cli = load_script("odysseus-notes")
    note = SimpleNamespace(
        id="n1",
        title="Errands",
        content="- [ ] Post office\n  - [x] Stamps\n```\n- [ ] not a task\n```",
        items=None,
        note_type="todo",
        color=None,
        label=None,
        pinned=False,
        archived=False,
        due_date=None,
        source=None,
        created_at=None,
        updated_at=None,
    )

    out = cli._serialize(note)
    assert out["items"] == [
        {"index": 0, "text": "Post office", "done": False, "indent": 0},
        {"index": 1, "text": "Stamps", "done": True, "indent": 1},
    ]
    assert out["note_type"] == "note"


def test_create_writes_each_item_as_a_task_line(monkeypatch):
    added = []

    def note(**fields):
        defaults = dict(items=None, archived=False, due_date=None, created_at=None, updated_at=None)
        return SimpleNamespace(**{**defaults, **fields})

    db = make_core_db_stub(monkeypatch, attributes={"Note": note})
    session = db.SessionLocal.return_value
    session.add.side_effect = added.append
    cli = load_script("odysseus-notes")
    emitted = []
    monkeypatch.setattr(cli, "emit", lambda payload, args: emitted.append(payload))
    args = cli._build_parser().parse_args(
        ["create", "--title", "Errands", "--content", "Before leaving", "--item", "Post office", "--item", "Bank"]
    )

    args.func(args)

    assert added[0].content == "Before leaving\n\n- [ ] Post office\n- [ ] Bank"
    assert added[0].items is None
    assert emitted[0]["items"] == [
        {"index": 0, "text": "Post office", "done": False, "indent": 0},
        {"index": 1, "text": "Bank", "done": False, "indent": 0},
    ]
    session.commit.assert_called_once()


def test_create_refuses_a_colour_off_the_palette(monkeypatch, capsys):
    make_core_db_stub(monkeypatch, models=["Note"])
    cli = load_script("odysseus-notes")
    opened = []
    monkeypatch.setattr(cli, "SessionLocal", lambda: opened.append(1), raising=False)
    args = SimpleNamespace(title="t", content="", item=[], type="note", color='red" onmouseover="x', label=None, pin=False)
    try:
        cli.cmd_create(args)
    except SystemExit as exc:
        assert exc.code == 1
    else:
        raise AssertionError("cmd_create accepted a bad colour")
    assert "color must be" in capsys.readouterr().err
    assert opened == []
