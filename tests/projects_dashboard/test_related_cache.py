import sqlite3
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import projects_dashboard as pd  # noqa: E402


def make(tmp_path):
    home = tmp_path / 'hermes'
    home.mkdir()
    db = sqlite3.connect(home / 'state.db')
    db.execute('create table messages (session_id text, role text, tool_calls text)')
    db.commit()
    dash = pd.Dashboard(home, tmp_path / 'webui', tmp_path / 'component')
    return dash, db


def add_calls(db, sid, path, n):
    for _ in range(n):
        db.execute('insert into messages values (?,?,?)', (sid, 'assistant', '[{"path": "%s/file.py"}]' % path))
    db.commit()


def test_cache_is_reused_while_nothing_changed_and_refreshed_after_new_tool_calls(tmp_path):
    dash, db = make(tmp_path)
    scope = [str(tmp_path / 'proj')]
    add_calls(db, 's1', (tmp_path / 'proj').as_posix(), 3)
    first = dash.related_sessions(scope, set())
    assert [r['id'] for r in first] == ['s1']
    db.execute('delete from messages'); db.commit()          # file stamp changes -> must not serve the old answer
    assert dash.related_sessions(scope, set()) == []
    add_calls(db, 's2', (tmp_path / 'proj').as_posix(), 4)
    assert [r['id'] for r in dash.related_sessions(scope, set())] == ['s2']


def test_a_different_folder_set_never_reuses_another_projects_answer_and_exclusions_apply_after_caching(tmp_path):
    dash, db = make(tmp_path)
    add_calls(db, 's1', (tmp_path / 'a').as_posix(), 3)
    assert [r['id'] for r in dash.related_sessions([str(tmp_path / 'a')], set())] == ['s1']
    assert dash.related_sessions([str(tmp_path / 'b')], set()) == []
    assert dash.related_sessions([str(tmp_path / 'a')], {'s1'}) == []         # a conversation placed in the project later drops out at once


def test_the_cache_is_bounded(tmp_path):
    dash, db = make(tmp_path)
    for i in range(pd.RELATED_CACHE_MAX * 2 + 5):
        dash.related_sessions([str(tmp_path / ('p%d' % i))], set())
    assert len(dash._related_cache) <= pd.RELATED_CACHE_MAX
