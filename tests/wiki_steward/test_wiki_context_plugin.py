"""odysseus-wiki-context: project detection, relevance, labels, read-only behaviour, failure handling, and the full
start -> work -> propose -> verify -> record -> fresh-session retrieval loop through the real MCP server.
Everything runs against a throwaway wiki and a throwaway project repository; the real wiki is never touched."""
import importlib.util
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import time
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PLUGIN = ROOT / 'integrations' / 'wiki' / 'plugins' / 'odysseus-wiki-context' / '__init__.py'
SRC = ROOT / 'src'
MCP = ROOT / 'scripts' / 'wiki_mcp.py'
GIT = os.environ.get('GIT_EXE') or r'C:\Program Files\Git\cmd\git.exe'
if not os.path.exists(GIT):
    GIT = 'git'
sys.path.insert(0, str(SRC))
import wiki_steward as ws  # noqa: E402

CAPS = ['search', 'propose', 'verify', 'record_task', 'handoff', 'status', 'conflicts', 'inbox', 'ack']


def git(repo, *args):
    return subprocess.run([GIT, '-c', 'user.name=t', '-c', 'user.email=t@localhost', '-C', str(repo), *args], capture_output=True, text=True, check=True).stdout.strip()


def load_plugin(name='wiki_context_under_test'):
    spec = importlib.util.spec_from_file_location(name, PLUGIN)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class Base(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=os.environ.get('TMPDIR'))
        t = Path(self.tmp.name)
        self.proj, self.other, self.wiki_root, self.home = t / 'proj', t / 'other', t / 'wiki', t / 'home'
        for d in (self.proj / 'src', self.other, self.home):
            d.mkdir(parents=True)
        (self.proj / 'src' / 'billing.py').write_text('# Invoices are rounded half-up to two decimals before tax.\nROUNDING = "half-up"\n', encoding='utf-8')
        (self.proj / 'src' / 'cache.py').write_text('# The session cache expires entries after 900 seconds.\nTTL = 900\n', encoding='utf-8')
        git(self.proj, 'init', '-q'); git(self.proj, 'add', '-A'); git(self.proj, 'commit', '-q', '-m', 'init')
        self._env = dict(os.environ)
        ws.Wiki.init(str(self.wiki_root), projects={'shop': {'repo': str(self.proj)}},
                     agents={'hermes': {'projects': ['shop'], 'can': CAPS}, 'other-agent': {'projects': [], 'can': ['search']}})
        self.wiki = ws.Wiki(str(self.wiki_root))
        self.wiki.adopt('user', 'configure')
        self.db = self.home / 'state.db'
        con = sqlite3.connect(self.db)
        con.execute('create table sessions (id text primary key, cwd text, git_repo_root text)')
        con.executemany('insert into sessions values (?,?,?)', [
            ('s-shop', str(self.proj / 'src'), str(self.proj)), ('s-chat', str(self.other), None), ('s-new', str(self.proj), str(self.proj))])
        con.commit(); con.close()
        os.environ['HERMES_HOME'] = str(self.home)
        self.write_config()

    def tearDown(self):
        os.environ.clear(); os.environ.update(self._env)
        self.tmp.cleanup()

    def write_config(self, **over):
        cfg = {'wiki_root': str(self.wiki_root), 'steward_src': str(SRC), 'agent': 'hermes', 'timeout_s': 5,
               'projects': {'shop': {'workspaces': [str(self.proj)]}}}
        cfg.update(over)
        (self.home / 'wiki-context.json').write_text(json.dumps(cfg), encoding='utf-8')

    def propose(self, claim, path, quote, subject, kind='fact'):
        return self.wiki.propose('hermes', claim, [{'type': 'file', 'path': path, 'contains': quote}], 'shop', subject=subject, kind=kind, scope='source')


class PluginTests(Base):
    def test_unrelated_chat_makes_no_wiki_call(self):
        plugin = load_plugin()
        plugin._search = lambda *a, **k: self.fail('wiki must not be consulted for an unrelated chat')
        self.assertIsNone(plugin.pre_llm_call(session_id='s-chat', user_message='how do rounding rules work in invoices', is_first_turn=True))
        self.assertIsNone(plugin.pre_llm_call(session_id='unknown-session', user_message='how do rounding rules work in invoices'))

    def test_no_config_is_inert(self):
        (self.home / 'wiki-context.json').unlink()
        self.assertIsNone(load_plugin().pre_llm_call(session_id='s-shop', user_message='invoice rounding rules', is_first_turn=True))

    def test_first_turn_announces_once_and_retrieves_only_relevant_verified_claim(self):
        a = self.propose('src/billing.py rounds invoices half-up to two decimals before tax.', 'src/billing.py', 'half-up to two decimals', 'shop.billing.rounding')
        self.propose('src/cache.py expires session cache entries after 900 seconds.', 'src/cache.py', 'after 900 seconds', 'shop.cache.ttl')
        self.assertEqual(a['status'], 'verified')
        plugin = load_plugin()
        out = plugin.pre_llm_call(session_id='s-shop', user_message='How are invoice amounts rounded in billing?', is_first_turn=True)['context']
        self.assertIn('wiki-knowledge-maintenance', out); self.assertIn('wiki_begin', out); self.assertIn('wiki_record_task', out)
        self.assertIn('[VERIFIED source-confirmed, fresh]', out); self.assertIn(a['id'], out)
        self.assertIn('billing.py', out); self.assertNotIn('cache.py', out)
        self.assertLess(len(out), 2700)
        again = plugin.pre_llm_call(session_id='s-shop', user_message='How are invoice amounts rounded in billing?')
        self.assertIsNone(again, 'the start note and an already shown claim are not repeated')
        later = plugin.pre_llm_call(session_id='s-shop', user_message='And how long does the session cache keep entries?')['context']
        self.assertIn('cache.py', later); self.assertNotIn('wiki_begin', later); self.assertNotIn('billing.py', later)

    def test_labels_pending_stale_and_conflict_are_distinct(self):
        self.propose('src/billing.py rounds invoices half-up to two decimals before tax.', 'src/billing.py', 'half-up to two decimals', 'shop.billing.rounding')
        pending = self.propose('src/cache.py decision: session cache expires entries after 900 seconds.', 'src/cache.py', 'after 900 seconds', 'shop.cache.ttl', kind='decision')
        self.assertEqual(pending['status'], 'needs-review')
        out = load_plugin().pre_llm_call(session_id='s-shop', user_message='session cache expires entries after how many seconds', is_first_turn=True)['context']
        self.assertIn('PENDING REVIEW', out); self.assertNotIn('[VERIFIED', out)
        (self.proj / 'src' / 'billing.py').write_text('# Invoices are rounded half-even.\nROUNDING = "half-even"\n', encoding='utf-8')
        git(self.proj, 'add', '-A'); git(self.proj, 'commit', '-q', '-m', 'change rounding')
        stale = load_plugin('p2').pre_llm_call(session_id='s-shop', user_message='invoice rounding half-up decimals tax', is_first_turn=True)['context']
        self.assertIn('STALE', stale); self.assertNotIn('[VERIFIED', stale)
        self.propose('src/billing.py rounds invoices half-even to two decimals before tax.', 'src/billing.py', 'half-even', 'shop.billing.rounding')
        both = load_plugin('p3').pre_llm_call(session_id='s-shop', user_message='invoice rounding decimals tax', is_first_turn=True)['context']
        self.assertTrue('CONFLICT' in both or 'STALE' in both, both)

    def test_hook_is_read_only(self):
        self.propose('src/billing.py rounds invoices half-up to two decimals before tax.', 'src/billing.py', 'half-up to two decimals', 'shop.billing.rounding')
        before = (git(self.wiki_root, 'rev-parse', 'HEAD'), git(self.wiki_root, 'status', '--porcelain'), git(self.proj, 'rev-parse', 'HEAD'))
        plugin = load_plugin()
        for n in range(4):
            plugin.pre_llm_call(session_id='s-shop', user_message='invoice rounding decimals tax %d' % n, is_first_turn=n == 0)
        after = (git(self.wiki_root, 'rev-parse', 'HEAD'), git(self.wiki_root, 'status', '--porcelain'), git(self.proj, 'rev-parse', 'HEAD'))
        self.assertEqual(before, after)
        self.assertEqual(self.wiki.integrity(), [])

    def test_project_boundary_follows_the_wiki_permissions(self):
        self.propose('src/billing.py rounds invoices half-up to two decimals before tax.', 'src/billing.py', 'half-up to two decimals', 'shop.billing.rounding')
        self.write_config(agent='other-agent')
        out = load_plugin().pre_llm_call(session_id='s-shop', user_message='invoice rounding decimals tax', is_first_turn=True)['context']
        self.assertNotIn('[VERIFIED', out); self.assertIn('unavailable', out)

    def test_unavailable_wiki_notes_once_and_never_raises(self):
        self.write_config(wiki_root=str(self.wiki_root / 'missing'))
        plugin = load_plugin()
        first = plugin.pre_llm_call(session_id='s-shop', user_message='invoice rounding decimals tax', is_first_turn=True)['context']
        self.assertIn('unavailable', first); self.assertIn('wiki_begin', first)
        self.assertIsNone(plugin.pre_llm_call(session_id='s-shop', user_message='invoice rounding decimals tax again'))
        self.write_config(steward_src=str(self.tmp.name))
        self.assertIn('unavailable', load_plugin('p4').pre_llm_call(session_id='s-shop', user_message='invoice rounding decimals tax', is_first_turn=True)['context'])

    def test_slow_wiki_times_out_without_blocking(self):
        plugin = load_plugin()
        plugin._search = lambda *a, **k: time.sleep(3) or []
        self.write_config(timeout_s=0.3)
        started = time.monotonic()
        out = plugin.pre_llm_call(session_id='s-shop', user_message='invoice rounding decimals tax', is_first_turn=True)['context']
        self.assertLess(time.monotonic() - started, 1.5); self.assertIn('timed out', out)

    def test_short_or_empty_messages_cost_nothing(self):
        plugin = load_plugin()
        plugin.pre_llm_call(session_id='s-shop', user_message='ok', is_first_turn=True)
        plugin._search = lambda *a, **k: self.fail('no search for a trivial message')
        self.assertIsNone(plugin.pre_llm_call(session_id='s-shop', user_message='ok thanks'))
        self.assertIsNone(plugin.pre_llm_call(session_id='s-shop', user_message=None))


class LoopThroughRealMcpServer(Base):
    """Phase 3 loop: an agent session works through the REAL wiki MCP server; a brand-new session (new module state, new session id) retrieves the result."""

    def rpc(self, proc, mid, method, params=None):
        proc.stdin.write(json.dumps({'jsonrpc': '2.0', 'id': mid, 'method': method, 'params': params or {}}) + '\n'); proc.stdin.flush()
        return json.loads(proc.stdout.readline())

    def call(self, proc, mid, name, args):
        reply = self.rpc(proc, mid, 'tools/call', {'name': name, 'arguments': args})
        return json.loads(reply['result']['content'][0]['text']), reply['result']['isError']

    def test_begin_propose_verify_record_then_fresh_session_retrieves_it(self):
        env = {**os.environ, 'WIKI_ROOT': str(self.wiki_root), 'WIKI_AGENT': 'hermes', 'PYTHONUTF8': '1'}
        proc = subprocess.Popen([sys.executable, str(MCP)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True, encoding='utf-8', env=env)
        try:
            init = self.rpc(proc, 1, 'initialize', {'protocolVersion': '2025-03-26'})
            self.assertIn('wiki_begin', init['result']['instructions'])
            begun, err = self.call(proc, 2, 'wiki_begin', {'project': 'shop', 'summary': 'Check how invoices are rounded'}); self.assertFalse(err)
            found, _ = self.call(proc, 3, 'wiki_search', {'query': 'invoice rounding half-up', 'project': 'shop'})
            self.assertEqual(found['result'], [], 'nothing known yet')
            proposal, err = self.call(proc, 4, 'wiki_propose', {
                'claim': 'src/billing.py rounds invoices half-up to two decimals before tax.', 'project': 'shop', 'subject': 'shop.billing.rounding',
                'scope': 'source', 'evidence': [{'type': 'file', 'path': 'src/billing.py', 'contains': 'half-up to two decimals'}]}); self.assertFalse(err)
            self.assertEqual((proposal['status'], proposal['grade']), ('verified', 'source-confirmed'))
            again, _ = self.call(proc, 5, 'wiki_verify', {'proposal_id': proposal['id']}); self.assertEqual(again['status'], 'verified')
            done, err = self.call(proc, 6, 'wiki_record_task', {'project': 'shop', 'summary': 'Checked invoice rounding', 'task_id': begun['task_id'], 'status': 'done'})
            self.assertFalse(err); self.assertFalse(done['duplicate'])
        finally:
            proc.stdin.close(); proc.wait(10)
        fresh = load_plugin('fresh_process')
        out = fresh.pre_llm_call(session_id='s-new', user_message='Why are invoice totals rounded this way?', is_first_turn=True)['context']
        self.assertIn(proposal['id'], out); self.assertIn('[VERIFIED source-confirmed, fresh]', out)
        task_files = list((self.wiki_root / 'tasks' / 'shop').glob('t-*.md'))
        self.assertEqual(len(task_files), 1)
        self.assertEqual(self.wiki.integrity(), [])


if __name__ == '__main__':
    unittest.main()
