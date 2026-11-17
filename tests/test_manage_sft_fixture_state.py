import importlib.util
import json
import sqlite3
import sys
from pathlib import Path


PATH = Path(__file__).resolve().parents[1] / "scripts" / "manage_sft_fixture_state.py"
SPEC = importlib.util.spec_from_file_location("manage_sft_fixture_state", PATH)
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


def make_db(path: Path) -> None:
    db = sqlite3.connect(path)
    db.executescript("""
        CREATE TABLE notes (id TEXT PRIMARY KEY, owner TEXT, title TEXT);
        CREATE TABLE documents (id TEXT PRIMARY KEY, owner TEXT, title TEXT);
        CREATE TABLE document_versions (id TEXT PRIMARY KEY, document_id TEXT, content TEXT);
        CREATE TABLE sessions (id TEXT PRIMARY KEY, owner TEXT, name TEXT);
    """)
    db.executemany("INSERT INTO notes VALUES (?,?,?)", [
        ("a", "alex", "baseline"), ("p", "pewds", "untouched"),
    ])
    db.execute("INSERT INTO documents VALUES ('d', 'alex', 'doc')")
    db.execute("INSERT INTO document_versions VALUES ('v', 'd', 'baseline body')")
    db.execute("INSERT INTO sessions VALUES ('s', 'alex', 'evidence')")
    db.commit()
    db.close()


def test_owner_restore_is_scoped_and_preserves_sessions(tmp_path):
    path = tmp_path / "app.db"
    make_db(path)
    snapshot = MODULE.snapshot_owner(path, "alex")
    db = sqlite3.connect(path)
    db.execute("DELETE FROM notes WHERE id='a'")
    db.execute("INSERT INTO notes VALUES ('junk', 'alex', 'audit junk')")
    db.execute("INSERT INTO sessions VALUES ('new', 'alex', 'new evidence')")
    db.commit()
    db.close()

    MODULE.restore_owner(path, snapshot, "alex")

    db = sqlite3.connect(path)
    assert db.execute("SELECT id,title FROM notes WHERE owner='alex'").fetchall() == [("a", "baseline")]
    assert db.execute("SELECT title FROM notes WHERE owner='pewds'").fetchone() == ("untouched",)
    assert db.execute("SELECT id FROM sessions WHERE owner='alex' ORDER BY id").fetchall() == [("new",), ("s",)]
    assert db.execute("SELECT content FROM document_versions").fetchone() == ("baseline body",)
    db.close()


def test_restore_rejects_cross_owner_snapshot(tmp_path):
    path = tmp_path / "app.db"
    make_db(path)
    snapshot = MODULE.snapshot_owner(path, "alex")
    try:
        MODULE.restore_owner(path, snapshot, "pewds")
    except ValueError as exc:
        assert "owner" in str(exc)
    else:
        raise AssertionError("cross-owner restore was accepted")


def test_owner_restore_includes_external_fixture_state(tmp_path):
    path = tmp_path / "app.db"
    make_db(path)
    (tmp_path / "user_prefs.json").write_text(
        '{"_users":{"alex":{"theme":"dark"},"pewds":{"theme":"light"}}}', encoding="utf-8",
    )
    (tmp_path / "fixture_email_messages.json").write_text(
        '{"messages":[{"owner":"alex","uid":"a"},{"owner":"pewds","uid":"p"}]}', encoding="utf-8",
    )
    (tmp_path / "email_blocked_senders.json").write_text(
        '{"owners":{"alex":["bad@example.com"],"pewds":[]}}', encoding="utf-8",
    )
    skill_dir = tmp_path / "skills" / "general" / "alex-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: alex-skill\nowner: alex\n---\nbody", encoding="utf-8")
    (tmp_path / "skills" / "_usage.json").write_text(
        '{"alex::alex-skill":{"uses":2},"pewds::other":{"uses":1}}', encoding="utf-8",
    )
    snapshot = MODULE.snapshot_owner(path, "alex", tmp_path)

    (tmp_path / "user_prefs.json").write_text('{"_users":{"alex":{"theme":"wrong"}}}', encoding="utf-8")
    (tmp_path / "fixture_email_messages.json").write_text('{"messages":[]}', encoding="utf-8")
    (skill_dir / "SKILL.md").write_text("---\nname: alex-skill\nowner: alex\n---\nwrong", encoding="utf-8")
    MODULE.restore_owner(path, snapshot, "alex", tmp_path)

    assert json.loads((tmp_path / "user_prefs.json").read_text())["_users"]["alex"] == {"theme": "dark"}
    emails = json.loads((tmp_path / "fixture_email_messages.json").read_text())["messages"]
    assert emails == [{"owner": "alex", "uid": "a"}]
    assert (skill_dir / "SKILL.md").read_text().endswith("body")
    usage = json.loads((tmp_path / "skills" / "_usage.json").read_text())
    assert usage["alex::alex-skill"] == {"uses": 2}
