"""odysseus-wiki-context through the REAL Hermes pipeline: plugin discovery, ``plugins.enabled``, and the agent's own pre_llm_call collector
(agent.turn_context._collect_pre_llm_call_context), in a disposable HERMES_HOME. No model is called, nothing in the live Hermes home is read or written.
Skips itself when the managed Hermes (component.json) is not installed."""
import json
import os
import shutil
import subprocess
import textwrap
import unittest
from pathlib import Path

from tests.wiki_steward.test_wiki_context_plugin import Base, PLUGIN

COMPONENT = Path(os.environ.get('LOCALAPPDATA', '')) / 'Odysseus' / 'hermes' / 'managed' / 'webapp' / 'component.json'
try:
    _C = json.loads(COMPONENT.read_text(encoding='utf-8-sig'))
    PY, AGENT = Path(_C['python']), Path(_C['agent'])
    AVAILABLE = PY.exists() and (AGENT / 'agent' / 'turn_context.py').exists()
except (OSError, ValueError, KeyError):
    AVAILABLE = False

PROBE = textwrap.dedent('''
    import json, sys, types
    sys.path.insert(0, sys.argv[1])
    from hermes_cli import plugins as P
    P.discover_plugins()
    from agent.turn_context import _collect_pre_llm_call_context as collect
    out = {}
    for sid, text in json.loads(sys.argv[2]):
        agent = types.SimpleNamespace(session_id=sid, model='m', platform='cli', _parent_session_id='', _user_id='', _persist_disabled=False)
        out[sid + '|' + text[:12]] = collect(agent, effective_task_id='t', turn_id='1', original_user_message=text, messages=[], conversation_history=None)
    print(json.dumps(out))
''')


@unittest.skipUnless(AVAILABLE, 'managed Hermes is not installed')
class RealHermesPipeline(Base):
    def run_probe(self, turns, enabled=True):
        plugin_dir = self.home / 'plugins' / 'odysseus-wiki-context'
        shutil.copytree(PLUGIN.parent, plugin_dir)
        (self.home / 'config.yaml').write_text('plugins:\n  enabled:\n%s' % ('    - odysseus-wiki-context\n' if enabled else '    - nothing\n'), encoding='utf-8')
        env = {k: v for k, v in os.environ.items() if k != 'PYTHONPATH'}
        env.update(HERMES_HOME=str(self.home), PYTHONUTF8='1')
        done = subprocess.run([str(PY), '-c', PROBE, str(AGENT), json.dumps(turns)], capture_output=True, text=True, env=env, timeout=120, cwd=str(self.home))
        self.assertEqual(done.returncode, 0, done.stderr[-800:])
        return json.loads(done.stdout.strip().splitlines()[-1])

    def test_tracked_session_gets_verified_claim_and_unrelated_session_gets_nothing(self):
        claim = self.propose('src/billing.py rounds invoices half-up to two decimals before tax.', 'src/billing.py', 'half-up to two decimals', 'shop.billing.rounding')
        out = self.run_probe([('s-shop', 'How are invoices rounded in billing?'), ('s-chat', 'How are invoices rounded in billing?')])
        tracked, other = out['s-shop|How are invo'], out['s-chat|How are invo']
        self.assertIn('wiki-knowledge-maintenance', tracked); self.assertIn(claim['id'], tracked); self.assertIn('[VERIFIED source-confirmed, fresh]', tracked)
        self.assertEqual(other, '')

    def test_missing_wiki_gives_one_short_note_not_an_error(self):
        self.write_config(wiki_root=str(self.wiki_root / 'missing'))
        out = self.run_probe([('s-shop', 'How are invoices rounded in billing?')])
        self.assertIn('unavailable', out['s-shop|How are invo'])

    def test_plugin_that_is_not_enabled_injects_nothing(self):
        self.propose('src/billing.py rounds invoices half-up to two decimals before tax.', 'src/billing.py', 'half-up to two decimals', 'shop.billing.rounding')
        out = self.run_probe([('s-shop', 'How are invoices rounded in billing?')], enabled=False)
        self.assertEqual(out['s-shop|How are invo'], '')


if __name__ == '__main__':
    unittest.main()
