"""Gateway guards carried by webapp-composer-control-center.patch: no workspace move under a running turn, no dangling active project.

The static checks always run. The behavioural checks execute the PATCHED Hermes source (set HERMES_PATCHED_SRC to a pin checkout with the four
patches applied, and HERMES_PATCHED_PYTHON to the Hermes interpreter); they are skipped otherwise."""
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
PATCH = (ROOT / 'integrations/hermes-mode/patches/webapp-composer-control-center.patch').read_text(encoding='utf-8')
SRC = os.environ.get('HERMES_PATCHED_SRC')
PY = os.environ.get('HERMES_PATCHED_PYTHON') or sys.executable


def test_patch_refuses_workspace_move_while_a_turn_runs_with_the_session_busy_reason():
    assert 'tui_gateway/methods_session.py' in PATCH
    assert 'live is not None and live.get("running")' in PATCH
    assert '"reason": "session_busy"' in PATCH


def test_patch_clears_the_active_pointer_with_the_delete_and_ignores_a_dangling_one():
    assert 'hermes_cli/projects_db.py' in PATCH
    assert 'DELETE FROM project_meta WHERE key = ? AND value = ?' in PATCH
    assert 'SELECT 1 FROM projects WHERE id = ?' in PATCH


def _run(code):
    with tempfile.TemporaryDirectory() as home:
        env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        env['HERMES_HOME'] = home
        out = subprocess.run([PY, '-c', code, SRC, home], capture_output=True, text=True, env=env, cwd=home, timeout=120)
        assert out.returncode == 0, out.stderr[-800:]
        rows = [json.loads(line[line.index('{"@"'):]) for line in (out.stdout + '\n' + out.stderr).splitlines() if '{"@"' in line]
        assert rows, out.stdout[-600:] + out.stderr[-600:]
        return rows


@pytest.mark.skipif(not SRC, reason='needs HERMES_PATCHED_SRC')
def test_running_session_refuses_workspace_move_and_keeps_its_folder_but_an_idle_one_moves():
    code = r"""
import sys, json, os
sys.path.insert(0, sys.argv[1])
from tui_gateway import server as s
tgt = os.path.join(sys.argv[2], 'tgt'); os.makedirs(tgt)
for running in (True, False):
    sess = {"session_key": "k%s" % running, "running": running, "cwd": "C:\\old", "profile_home": sys.argv[2], "agent": None}
    with s._sessions_lock: s._sessions["s%s" % running] = sess
    r = s._methods["session.workspace.move"](1, {"session_key": sess["session_key"], "cwd": tgt})
    print(json.dumps({"@": running, "error": r.get("error"), "moved": sess["cwd"] != "C:\\old"}))
"""
    busy, idle = _run(code)
    assert busy['error']['code'] == 4009 and busy['error']['data'] == {'reason': 'session_busy'} and busy['moved'] is False
    assert idle['error'] is None and idle['moved'] is True


@pytest.mark.skipif(not SRC, reason='needs HERMES_PATCHED_SRC')
def test_deleting_the_active_project_leaves_no_pointer_and_a_stale_pointer_reads_empty():
    code = r"""
import sys, json
sys.path.insert(0, sys.argv[1])
from hermes_cli import projects_db as pdb
with pdb.connect_closing() as c:
    a = pdb.create_project(c, name='A'); b = pdb.create_project(c, name='B'); a = getattr(a, 'id', a); b = getattr(b, 'id', b)
    pdb.set_active(c, a); pdb.delete_project(c, b); keep = pdb.get_active_id(c) == a
    pdb.delete_project(c, a); gone = pdb.get_active_id(c); rows = c.execute("select count(*) from project_meta where key='active_id'").fetchone()[0]
    pdb.set_active(c, 'p_missing'); stale = pdb.get_active_id(c)
print(json.dumps({"@": 1, "keep": keep, "gone": gone, "rows": rows, "stale": stale}))
"""
    (r,) = _run(code)
    assert r['keep'] is True and r['gone'] is None and r['rows'] == 0 and r['stale'] is None
