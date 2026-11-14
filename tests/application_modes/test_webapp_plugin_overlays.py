"""Reviewed plugin overlays of the managed Webapp component: the Wiki context plugin and the hermes-security skill-description patch.
Everything runs in disposable homes. The installed hermes-security plugin is only READ (copied as a fixture) and its file hash is asserted unchanged."""
import hashlib
import json
import os
import shutil
import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from src.hermes_component import ComponentError
from tests.application_modes.test_webapp_component_settings import Base, _hermes_runtime_python

LIVE_SECURITY = Path(os.environ.get('LOCALAPPDATA', '')) / 'Odysseus' / 'hermes' / 'state' / 'hermes-gateway' / 'plugins' / 'hermes-security'
LIVE_FILE = LIVE_SECURITY / 'hermes_security' / 'plugin.py'


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest() if Path(path).exists() else None


@unittest.skipUnless(_hermes_runtime_python(), 'no Hermes runtime python with ruamel.yaml is installed here')
class WikiContextOverlay(Base):
    def setUp(self):
        super().setUp()
        self.runtime = _hermes_runtime_python()
        self.config['python'] = self.runtime
        self.save(self.config)
        self.wiki = Path(self.tmp.name) / 'wiki'
        self.wiki.mkdir()
        (self.wiki / 'steward.json').write_text('{}', encoding='utf-8')
        self.kw = dict(wiki_root=self.wiki, steward_src=ROOT / 'src', workspaces=[Path(self.tmp.name) / 'proj'], project='odysseus')

    def test_plan_writes_nothing(self):
        plan = self.component.wiki_context_plan(self.home, self.runtime, **self.kw)
        self.assertEqual((plan['plugin'], plan['config'], plan['enabled']), ('new', 'new', False))
        self.assertFalse((self.home / 'plugins').exists() or (self.home / 'wiki-context.json').exists())
        self.assertEqual(self.component.home_settings(self.runtime, self.home).get('plugins_enabled') or [], [])

    def test_install_copies_writes_settings_enables_and_is_idempotent(self):
        (self.home / 'config.yaml').write_text('_config_version: 53\nplugins:\n  enabled:\n    - agent-importers\n', encoding='utf-8')
        self.component.install_wiki_context_plugin(self.home, self.runtime, **self.kw)
        target = self.home / 'plugins' / 'odysseus-wiki-context'
        self.assertTrue((target / 'plugin.yaml').is_file() and (target / '__init__.py').is_file())
        written = json.loads((self.home / 'wiki-context.json').read_text(encoding='utf-8'))
        self.assertEqual(written['projects']['odysseus']['workspaces'], [str(Path(self.tmp.name) / 'proj')])
        self.assertEqual(self.component.home_settings(self.runtime, self.home)['plugins_enabled'], ['agent-importers', 'odysseus-wiki-context'])
        self.component.install_wiki_context_plugin(self.home, self.runtime, **self.kw)
        self.assertEqual(self.component.home_settings(self.runtime, self.home)['plugins_enabled'].count('odysseus-wiki-context'), 1)
        self.assertEqual(self.component.wiki_context_plan(self.home, self.runtime, **self.kw)['plugin'], 'same')

    def test_foreign_plugin_and_changed_settings_are_never_overwritten(self):
        target = self.home / 'plugins' / 'odysseus-wiki-context'
        target.mkdir(parents=True)
        (target / 'plugin.yaml').write_text('name: something-else\n', encoding='utf-8')
        with self.assertRaises(ComponentError):
            self.component.install_wiki_context_plugin(self.home, self.runtime, **self.kw)
        shutil.rmtree(target)
        (self.home / 'wiki-context.json').write_text(json.dumps({'wiki_root': 'X:/mine'}), encoding='utf-8')
        with self.assertRaises(ComponentError):
            self.component.install_wiki_context_plugin(self.home, self.runtime, **self.kw)
        self.assertEqual(json.loads((self.home / 'wiki-context.json').read_text(encoding='utf-8')), {'wiki_root': 'X:/mine'})
        self.assertFalse((self.home / 'plugins' / 'odysseus-wiki-context').exists(), 'nothing was copied before the refusal')

    def test_not_a_wiki_is_refused(self):
        with self.assertRaises(ComponentError):
            self.component.wiki_context_plan(self.home, self.runtime, **{**self.kw, 'wiki_root': Path(self.tmp.name)})


@unittest.skipUnless(LIVE_FILE.is_file(), 'the hermes-security plugin is not installed here')
class SecurityPluginPatch(Base):
    def setUp(self):
        super().setUp()
        self.before = sha(LIVE_FILE)
        shutil.copytree(LIVE_SECURITY, self.home / 'plugins' / 'hermes-security', ignore=shutil.ignore_patterns('__pycache__', 'tests', 'evals'))
        self.plugin_py = self.home / 'plugins' / 'hermes-security' / 'hermes_security' / 'plugin.py'

    def tearDown(self):
        self.assertEqual(sha(LIVE_FILE), self.before, 'the live installed plugin must never change')

    def test_absent_plugin_is_reported_and_nothing_is_created(self):
        shutil.rmtree(self.home / 'plugins' / 'hermes-security')
        self.assertEqual(self.component.apply_security_plugin_patch(self.home, dry_run=False)['state'], 'absent')
        self.assertFalse((self.home / 'plugins' / 'hermes-security').exists())

    def test_dry_run_changes_nothing(self):
        if self.component.security_plugin_state(self.home) != 'pristine':
            self.skipTest('the installed plugin is already patched or modified')
        before = sha(self.plugin_py)
        report = self.component.apply_security_plugin_patch(self.home)
        self.assertEqual((report['state'], report['applied']), ('pristine', False))
        self.assertEqual(sha(self.plugin_py), before)

    def test_apply_builds_a_patched_copy_keeps_a_backup_and_is_idempotent(self):
        if self.component.security_plugin_state(self.home) != 'pristine':
            self.skipTest('the installed plugin is already patched or modified')
        original = self.plugin_py.read_bytes()
        report = self.component.apply_security_plugin_patch(self.home, dry_run=False)
        self.assertTrue(report['applied'])
        self.assertEqual(self.component.security_plugin_state(self.home), 'patched')
        self.assertEqual((Path(report['backup']) / 'hermes_security' / 'plugin.py').read_bytes(), original)
        self.assertIn('NousResearch', (self.home / 'plugins' / 'hermes-security' / 'ODYSSEUS_PATCH.txt').read_text(encoding='utf-8'))
        self.assertTrue((self.home / 'plugins' / 'hermes-security' / 'LICENSE').is_file(), 'upstream licence stays with the plugin')
        again = self.component.apply_security_plugin_patch(self.home, dry_run=False)
        self.assertEqual((again['state'], again['applied']), ('patched', False))

    def test_a_locally_edited_plugin_is_refused_and_left_alone(self):
        text = self.plugin_py.read_text(encoding='utf-8')
        if self.component.security_plugin_state(self.home) == 'patched':
            self.skipTest('already patched')
        self.plugin_py.write_text(text + '\n# local edit\n', encoding='utf-8')
        edited = sha(self.plugin_py)
        with self.assertRaises(ComponentError):
            self.component.apply_security_plugin_patch(self.home, dry_run=False)
        self.assertEqual(sha(self.plugin_py), edited)
        self.assertFalse(list((self.component.directory / 'staging').glob('hermes-security-*')), 'the staging copy is removed after a refusal')
        self.assertFalse((self.component.directory / 'backups').exists() and any((self.component.directory / 'backups').glob('hermes-security-*')))

    def test_a_failed_swap_restores_the_original(self):
        if self.component.security_plugin_state(self.home) != 'pristine':
            self.skipTest('the installed plugin is already patched or modified')
        original = sha(self.plugin_py)
        real = os.replace
        calls = []

        def flaky(src, dst):
            calls.append((str(src), str(dst)))
            if os.path.normcase(str(dst)) == os.path.normcase(str(self.home / 'plugins' / 'hermes-security')) and 'staging' in str(src):
                raise PermissionError('locked')
            return real(src, dst)
        with mock.patch('src.hermes_webapp_component.os.replace', side_effect=flaky):
            with self.assertRaises(ComponentError):
                self.component.apply_security_plugin_patch(self.home, dry_run=False)
        self.assertEqual(sha(self.plugin_py), original)
        self.assertEqual(self.component.security_plugin_state(self.home), 'pristine')


if __name__ == '__main__':
    unittest.main()
