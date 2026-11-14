"""Drive list and typed-path check for the Projects page (read-only, no listing of folder contents)."""
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src import projects_dashboard as pd  # noqa: E402
from src.hermes_local_api import LOCAL_PREFIX, LocalProjectsApi  # noqa: E402

DASH = pd.Dashboard(None, None, None)
DASH.write_roots = lambda: []
DASH.blocked_folders = lambda: []
DASH.legacy_roots = lambda: []
windows = os.name == 'nt'


def test_drives_report_a_filesystem_for_ready_volumes():
    drives = DASH.drives()
    assert drives and all({'path', 'label', 'filesystem', 'type', 'ready'} <= set(d) for d in drives)
    if windows:
        assert all(d['path'][1:] == ':\\' for d in drives) and any(d['ready'] and d['filesystem'] for d in drives)


def test_a_real_folder_is_valid_and_a_git_folder_reports_its_branch(tmp_path):
    folder = tmp_path / 'app'
    (folder / '.git').mkdir(parents=True)
    (folder / '.git' / 'HEAD').write_text('ref: refs/heads/main\n')
    plain = tmp_path / 'plain'
    plain.mkdir()
    git = DASH.check_path(str(folder))
    assert git['valid'] and git['kind'] == 'git' and git['git']['branch'] == 'main' and git['permission']['state'] == 'none' and git['filesystem']
    assert DASH.check_path(str(plain) + os.sep)['kind'] == 'folder'


def test_missing_file_relative_unc_and_junk_paths_are_explained_not_accepted(tmp_path):
    (tmp_path / 'f.txt').write_text('x')
    missing = DASH.check_path(str(tmp_path / 'later'))
    assert not missing['valid'] and missing['kind'] == 'missing' and 'does not exist yet' in missing['problem']
    assert 'file' in DASH.check_path(str(tmp_path / 'f.txt'))['problem']
    for bad in ('', '   ', 'relative\\path', 'app', '\\\\server\\share\\x', '//server/share', 'F:\\a\x00b', 'x' * 500):
        result = DASH.check_path(bad)
        assert not result['valid'] and result['problem'], bad
    if windows:
        assert 'not available' in DASH.check_path('Q:\\nowhere')['problem'] or DASH.check_path('Q:\\nowhere')['problem']


def test_a_granted_folder_reports_the_grant(tmp_path):
    DASH.write_roots = lambda: [str(tmp_path)]
    try:
        assert DASH.check_path(str(tmp_path))['permission']['state'] == 'granted'
    finally:
        DASH.write_roots = lambda: []


def test_routes_are_read_only_and_pass_the_query_through():
    seen = []

    class Fake:
        def drives(self):
            return [{'path': 'F:\\'}]

        def check_path(self, raw):
            seen.append(raw)
            return {'valid': True}

    def call(path, query=b'', method='GET', grant=object()):
        sent = []

        async def send(message):
            sent.append(message)
        scope = {'type': 'http', 'method': method, 'path': path, 'query_string': query, 'headers': [(b'host', b'hermes.localhost:7000')]}
        asyncio.run(LocalProjectsApi(lambda: Fake())(scope, None, send, grant))
        return sent[0]['status'], json.loads(sent[1]['body'])
    assert call(LOCAL_PREFIX + 'fs/drives') == (200, {'drives': [{'path': 'F:\\'}]})
    assert call(LOCAL_PREFIX + 'fs/check', b'path=F%3A%5COdysseus%20x') == (200, {'valid': True}) and seen == ['F:\\Odysseus x']
    assert call(LOCAL_PREFIX + 'fs/check', method='POST')[0] == 405 and call(LOCAL_PREFIX + 'fs/check', grant=None)[0] == 401
    assert call(LOCAL_PREFIX + 'fs/list')[0] == 404 and call(LOCAL_PREFIX + 'fs/check/x')[0] == 404


def test_wiki_claims_are_read_from_files_verified_first_and_unreadable_files_are_skipped(tmp_path):
    folder = tmp_path / 'claims' / 'demo'
    folder.mkdir(parents=True)
    for name, status, subject in (('c-1', 'needs-review', 'b.policy'), ('c-2', 'verified', 'a.fact'), ('c-3', 'verified', 'a.other')):
        (folder / (name + '.md')).write_text('---\n' + json.dumps({'id': name, 'claim': 'x' * 400, 'status': status, 'subject': subject, 'grade': 'source-confirmed',
                                                                  'verified_commit': 'abcdef0123456789', 'conflicts_with': []}) + '\n---\n', encoding='utf-8')
    (folder / 'c-bad.md').write_text('not a claim', encoding='utf-8')
    dash = pd.Dashboard(None, None, None, wiki_root=tmp_path)
    got = dash._wiki_claims('demo')
    assert got['counts'] == {'verified': 2, 'needs-review': 1}
    assert [c['id'] for c in got['items']] == ['c-2', 'c-3', 'c-1'] and len(got['items'][0]['claim']) == 260 and got['items'][0]['verified_commit'] == 'abcdef012'
    assert pd.Dashboard(None, None, None, wiki_root=tmp_path)._wiki_claims('other') == {'counts': {}, 'items': []}
    assert DASH._wiki_claims('demo') == {'counts': {}, 'items': []}
