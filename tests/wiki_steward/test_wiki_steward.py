import json
import os
import subprocess
import sys
import threading
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / 'src'))
import wiki_steward as ws  # noqa: E402

FIX = 'def delete_cookbook(path):\n    return remove_tree(path)  # fixed: handles read-only files on Windows\n'
CAN = list(ws.CAPS)


def src_git(repo, *args):
    return ws.git(repo, '-c', 'user.name=dev', '-c', 'user.email=d@x', *args).stdout.strip()


def commit(repo, name, text, msg):
    (repo / name).parent.mkdir(parents=True, exist_ok=True)
    (repo / name).write_text(text, encoding='utf-8', newline='\n')
    src_git(repo, 'add', '-A')
    src_git(repo, 'commit', '-q', '-m', msg)
    return src_git(repo, 'rev-parse', 'HEAD')


@pytest.fixture
def env(tmp_path):
    repo = tmp_path / 'source'
    repo.mkdir()
    src_git(repo, 'init', '-q', '-b', 'main')
    fix = commit(repo, 'src/cookbook.py', FIX, 'Fix cookbook delete on Windows')
    agents = {n: {'projects': ['odysseus'], 'can': CAN} for n in ('sonnet', 'longcat', 'wiki-bot')}
    agents['guest'] = {'projects': ['other'], 'can': ['search']}
    wiki = ws.Wiki.init(tmp_path / 'wiki', projects={'odysseus': {'repo': str(repo)}, 'other': {'repo': str(repo)}},
                        agents=agents, reviewers=['user'])
    wiki.adopt('user', 'configure')
    return wiki, repo, fix


def test_verified_fact_is_searchable_and_fresh(env):
    wiki, repo, fix = env
    r = wiki.propose('sonnet', 'Cookbook delete on Windows is fixed by handling read-only files.', [{'type': 'commit', 'ref': fix[:10]}],
                     'odysseus', subject='windows.cookbook-delete')
    assert r['status'] == 'verified'
    hit = wiki.search('longcat', 'cookbook delete windows')[0]
    assert hit['id'] == r['id'] and hit['freshness'] == 'fresh' and hit['note'] is None and hit['verified_commit']
    assert not wiki.integrity()


def test_two_agents_conflicting_claims(env):
    wiki, repo, fix = env
    a = wiki.propose('sonnet', 'Cookbook delete on Windows works after the fix.', [{'type': 'commit', 'ref': fix}], 'odysseus', subject='windows.cookbook-delete')
    b = wiki.propose('longcat', 'Cookbook delete still fails on Windows for read-only files.', [{'type': 'note', 'text': 'saw it fail'}],
                     'odysseus', subject='windows.cookbook-delete')
    assert a['status'] == 'verified' and b['status'] == 'conflict'
    c = wiki.propose('longcat', 'Cookbook delete fails on Windows when a file is read-only.', [{'type': 'file', 'path': 'src/cookbook.py', 'contains': 'delete_cookbook'}],
                     'odysseus', subject='windows.cookbook-delete')
    assert c['status'] == 'conflict', 'mechanical evidence alone must not displace a verified claim'
    out = wiki.conflicts('sonnet')
    assert {x['id'] for x in out['contradictions']} == {b['id'], c['id']}
    assert [h['id'] for h in wiki.search('sonnet', 'cookbook delete windows')] == [a['id']]
    shown = {h['id']: h for h in wiki.search('sonnet', 'cookbook delete fails', include_provisional=True)}
    assert shown[b['id']]['note'].startswith('NOT VERIFIED')
    with pytest.raises(ws.Denied):
        wiki.decide('longcat', b['id'], 'accept')
    with pytest.raises(ws.Denied):
        wiki.decide('wiki-bot', c['id'], 'accept')


def test_freshness_flags_changed_code_and_bot_resolves_conflict(env):
    wiki, repo, fix = env
    a = wiki.propose('sonnet', 'Cookbook delete on Windows works after the fix.', [{'type': 'commit', 'ref': fix}], 'odysseus', subject='windows.cookbook-delete')
    c = wiki.propose('longcat', 'Cookbook delete fails on Windows when a file is read-only.', [{'type': 'file', 'path': 'src/cookbook.py', 'contains': 'delete_cookbook'}],
                     'odysseus', subject='windows.cookbook-delete')
    commit(repo, 'src/cookbook.py', 'def delete_cookbook(path):\n    raise OSError("regressed")\n', 'Refactor cookbook')
    hit = wiki.search('sonnet', 'cookbook delete windows')[0]
    assert hit['freshness'] == 'stale' and hit['changed'] == ['src/cookbook.py'] and 'STALE' in hit['note']
    assert wiki.search('sonnet', 'cookbook delete windows', fresh_only=True) == []
    assert wiki.status('sonnet', 'odysseus')['stale'] == [a['id']]
    assert wiki.conflicts('sonnet')['outdated'][0]['id'] == a['id']
    again = wiki.recheck('sonnet', a['id'])
    assert again['status'] == 'stale' and 'stale-needs-reverification' in again['reasons'], 'a commit alone cannot re-prove the claim'
    wiki.verify('longcat', c['id'])
    assert wiki.decide('wiki-bot', c['id'], 'accept', 'old claim is stale')['status'] == 'verified'
    assert wiki._get(a['id'])['status'] == 'superseded'
    assert wiki.search('sonnet', 'cookbook delete')[0]['id'] == c['id']
    assert not wiki.integrity()


def test_content_bound_evidence_can_be_rechecked_after_unrelated_change(env):
    wiki, repo, fix = env
    r = wiki.propose('sonnet', 'The cookbook module defines delete_cookbook.', [{'type': 'file', 'path': 'src/cookbook.py', 'contains': 'def delete_cookbook'}], 'odysseus')
    commit(repo, 'src/cookbook.py', FIX + '\n# comment\n', 'Add comment')
    assert wiki.search('sonnet', 'delete_cookbook')[0]['freshness'] == 'stale'
    assert wiki.recheck('sonnet', r['id'])['status'] == 'verified'
    assert wiki.search('sonnet', 'delete_cookbook')[0]['freshness'] == 'fresh'


def test_sensitive_claims_need_review_and_agents_cannot_promote(env):
    wiki, repo, fix = env
    r = wiki.propose('sonnet', 'Workspace grants take effect without a restart.', [{'type': 'commit', 'ref': fix}], 'odysseus')
    assert r['status'] == 'needs-review' and 'sensitive' in r['reasons']
    d = wiki.propose('sonnet', 'Decision: use one wiki steward.', [{'type': 'commit', 'ref': fix}], 'odysseus', kind='decision')
    assert d['status'] == 'needs-review'
    for who in ('sonnet', 'wiki-bot'):
        with pytest.raises(ws.Denied):
            wiki.decide(who, r['id'], 'accept')
    assert wiki.decide('user', r['id'], 'accept')['status'] == 'verified'


def test_unverifiable_stays_provisional_and_bad_evidence_is_rejected(env):
    wiki, repo, fix = env
    assert wiki.propose('sonnet', 'The nightly job takes about ten minutes.', [{'type': 'note', 'text': 'observed'}], 'odysseus')['status'] == 'provisional'
    bad = wiki.propose('sonnet', 'Something was fixed in a commit.', [{'type': 'commit', 'ref': 'deadbeef'}], 'odysseus')
    assert bad['status'] == 'provisional' and 'evidence-failed' in bad['reasons']
    for path in ('../secret.txt', '.env', 'C:/Windows/win.ini', 'src/missing.py'):
        r = wiki.propose('sonnet', 'File based claim about %s' % path.replace('/', '_'), [{'type': 'file', 'path': path}], 'odysseus')
        assert r['status'] == 'provisional' and 'evidence-failed' in r['reasons']
    assert wiki.search('sonnet', 'nightly job') == []


def test_secrets_are_refused_and_nothing_is_written(env):
    wiki, repo, fix = env
    before = git_head(wiki)
    for text in ('The password: hunter2hunter2 works.', 'Use sk-abcdefghijklmnopqrstuvwxyz123456 for it.', 'Bearer abcdefghijklmnopqrstuvwxyz0123'):
        with pytest.raises(ws.WikiError):
            wiki.propose('sonnet', text, [], 'odysseus')
    assert git_head(wiki) == before and not wiki.integrity()


def git_head(wiki):
    return ws.git(wiki.root, 'rev-parse', 'HEAD').stdout.strip()


def test_duplicates_merge_into_one_claim(env):
    wiki, repo, fix = env
    a = wiki.propose('sonnet', 'Cookbook delete on Windows is fixed.', [{'type': 'commit', 'ref': fix}], 'odysseus', subject='x.y')
    b = wiki.propose('longcat', 'cookbook delete on windows is FIXED!', [{'type': 'note'}], 'odysseus', subject='x.y')
    assert b['duplicate'] and b['id'] == a['id']
    rec = wiki._get(a['id'])
    assert rec['agents'] == ['sonnet', 'longcat'] and len(rec['corroborations']) == 1 and len(wiki._claims('odysseus')) == 1


def test_authorization_and_project_scoping(env):
    wiki, repo, fix = env
    with pytest.raises(ws.Denied):
        wiki.propose('stranger', 'Some claim that is long enough.', [], 'odysseus')
    with pytest.raises(ws.Denied):
        wiki.propose('guest', 'Some claim that is long enough.', [], 'other')
    with pytest.raises(ws.Denied):
        wiki.search('guest', 'cookbook', project='odysseus')
    r = wiki.propose('sonnet', 'Cookbook delete on Windows is fixed.', [{'type': 'commit', 'ref': fix}], 'odysseus')
    assert wiki.search('guest', 'cookbook delete') == []
    assert r['status'] == 'verified'


def test_record_task_sync_handoff_inbox(env):
    wiki, repo, fix = env
    t = wiki.record_task('sonnet', 'odysseus', 'Fixed cookbook delete', commits=[fix, 'deadbeef'], tests=['test_cookbook'])
    assert t['unverified_commits'] == ['deadbeef']
    assert wiki.record_task('sonnet', 'odysseus', 'Fixed cookbook delete', commits=[fix, 'deadbeef'], tests=['test_cookbook'])['duplicate']
    assert wiki.sync('sonnet', 'odysseus')['recorded'] == 1
    commit(repo, 'a.txt', 'x', 'Second change')
    assert wiki.sync('sonnet', 'odysseus')['recorded'] == 1 and wiki.sync('sonnet', 'odysseus')['recorded'] == 0
    c = wiki.propose('longcat', 'Cookbook delete fails on Windows.', [], 'odysseus', subject='s')
    wiki.propose('sonnet', 'Cookbook delete works on Windows.', [{'type': 'commit', 'ref': fix}], 'odysseus', subject='s')
    h = wiki.handoff('sonnet', 'longcat', 'odysseus', 'Continue the Windows port', next_steps=['run the baseline'], refs={'claims': [c['id']]})
    assert c['id'] in [x['id'] for x in h['attention']]
    assert [x['id'] for x in wiki.inbox('longcat')] == [h['id']] and wiki.inbox('sonnet') == []
    wiki.ack('longcat', h['id'])
    assert wiki.inbox('longcat') == []
    st = wiki.status('sonnet', 'odysseus')
    assert len(st['recent_tasks']) == 3 and st['open_handoffs'] == [] and st['open_conflicts'] == 1


def test_out_of_band_edit_blocks_writes_until_adopted(env):
    wiki, repo, fix = env
    r = wiki.propose('sonnet', 'Cookbook delete on Windows is fixed.', [{'type': 'commit', 'ref': fix}], 'odysseus')
    path = wiki.root / 'claims' / 'odysseus' / (r['id'] + '.md')
    path.write_text(path.read_text(encoding='utf-8').replace('"verified"', '"rejected"'), encoding='utf-8')
    assert any('uncommitted' in p for p in wiki.integrity())
    with pytest.raises(ws.IntegrityError):
        wiki.propose('longcat', 'Another claim that is long enough.', [], 'odysseus')
    with pytest.raises(ws.Denied):
        wiki.adopt('longcat', 'sneaky')
    wiki.adopt('user', 'reviewed manual change')
    assert not wiki.integrity()


def test_foreign_commit_is_detected(env):
    wiki, repo, fix = env
    (wiki.root / 'note.md').write_text('hi', encoding='utf-8')
    ws.git(wiki.root, 'add', '-A')
    ws.git(wiki.root, '-c', 'user.name=someone', '-c', 'user.email=a@b', 'commit', '-q', '-m', 'direct edit')
    assert any('not made by the steward' in p for p in wiki.integrity())


def test_concurrent_writers_keep_the_wiki_consistent(env):
    wiki, repo, fix = env
    errors = []

    def work(n):
        try:
            ws.Wiki(wiki.root).propose('sonnet' if n % 2 else 'longcat', 'Independent finding number %d about module %d.' % (n, n * 7),
                                       [{'type': 'commit', 'ref': fix}], 'odysseus', subject='finding.%d' % n)
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=work, args=(n,)) for n in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert not errors and len(wiki._claims('odysseus')) == 8 and not wiki.integrity()


def test_failed_write_leaves_no_trace_and_revert_is_reversible(env, monkeypatch):
    wiki, repo, fix = env
    head = git_head(wiki)
    monkeypatch.setattr(ws.Wiki, '_audit', lambda *a, **k: (_ for _ in ()).throw(RuntimeError('disk full')))
    with pytest.raises(RuntimeError):
        wiki.propose('sonnet', 'Cookbook delete on Windows is fixed.', [{'type': 'commit', 'ref': fix}], 'odysseus')
    monkeypatch.undo()
    assert git_head(wiki) == head and wiki._claims('odysseus') == [] and not wiki.integrity()
    r = wiki.propose('sonnet', 'Cookbook delete on Windows is fixed.', [{'type': 'commit', 'ref': fix}], 'odysseus')
    commit_sha = git_head(wiki)
    with pytest.raises(ws.Denied):
        wiki.revert('sonnet', commit_sha)
    wiki.revert('user', commit_sha)
    assert wiki._claims('odysseus') == [] and not wiki.integrity()
    assert r['status'] == 'verified'


def test_cli_and_mcp_entry_points(env):
    wiki, repo, fix = env
    wiki.propose('sonnet', 'Cookbook delete on Windows is fixed.', [{'type': 'commit', 'ref': fix}], 'odysseus')
    base = {**os.environ, 'WIKI_ROOT': str(wiki.root), 'WIKI_AGENT': 'longcat', 'PYTHONPATH': ''}
    cli = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'wiki.py'), 'call', 'search', '--args', '{"query": "cookbook delete"}'],
                         capture_output=True, text=True, env=base)
    assert json.loads(cli.stdout)[0]['freshness'] == 'fresh'
    refused = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'wiki.py'), 'decide', 'c-x', 'accept'], capture_output=True, text=True, env=base, input='')
    assert refused.returncode == 1 and 'needs a terminal' in refused.stdout
    msgs = [{'jsonrpc': '2.0', 'id': 1, 'method': 'initialize', 'params': {}}, {'jsonrpc': '2.0', 'method': 'notifications/initialized'},
            {'jsonrpc': '2.0', 'id': 2, 'method': 'tools/list'},
            {'jsonrpc': '2.0', 'id': 3, 'method': 'tools/call', 'params': {'name': 'wiki_search', 'arguments': {'query': 'cookbook'}}},
            {'jsonrpc': '2.0', 'id': 4, 'method': 'tools/call', 'params': {'name': 'wiki_decide', 'arguments': {}}},
            {'jsonrpc': '2.0', 'id': 5, 'method': 'tools/call', 'params': {'name': 'wiki_search', 'arguments': {'query': 'x', 'agent': 'sonnet'}}}]
    out = subprocess.run([sys.executable, str(ROOT / 'scripts' / 'wiki_mcp.py')], capture_output=True, text=True, env=base,
                         input='\n'.join(json.dumps(m) for m in msgs) + '\n')
    replies = {r['id']: r for r in map(json.loads, out.stdout.splitlines())}
    assert len(replies) == 5 and 'wiki_decide' not in [t['name'] for t in replies[2]['result']['tools']]
    assert json.loads(replies[3]['result']['content'][0]['text'])[0]['project'] == 'odysseus'
    assert replies[4]['result']['isError'] and replies[5]['result']['isError'], 'unknown tools and an identity argument must be refused'
