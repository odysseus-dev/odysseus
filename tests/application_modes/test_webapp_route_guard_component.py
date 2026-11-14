"""Webapp component: the reviewed route guard is part of its source, and its state is backed up (grants are not)."""
import json
import subprocess
import tempfile
import types
import unittest
from unittest import mock
from pathlib import Path

from src.hermes_backup import create_backup, verify_backup
from src.hermes_component import apply_patch, patch_state, verify_checkout
from src.hermes_webapp_component import ROUTE_GUARD_PATCH, WebappComponent

ROOT = Path(__file__).resolve().parents[2]
GIT = ['git', '-c', 'user.name=t', '-c', 'user.email=t@example.invalid']


class RouteGuardComponentTests(unittest.TestCase):
    def test_guard_patch_ships_and_is_owned_by_the_webapp_component_only(self):
        import src.hermes_component as shared
        self.assertTrue((ROOT / ROUTE_GUARD_PATCH).is_file())
        self.assertNotIn('agent', shared._PATCHES, 'the WebUI component must never patch the live agent checkout')

    def test_guard_state_reflects_source_and_adopted_gateways_are_unpatched(self):
        manager = WebappComponent.__new__(WebappComponent)
        manager.application_root = ROOT
        self.assertEqual(manager.route_guard_state({'origin': 'adopted', 'agent': 'x'}), 'absent')
        self.assertEqual(manager.route_guard_state({'origin': 'managed', 'agent': str(ROOT / 'does-not-exist')}), 'unknown')

    def test_a_new_release_carries_the_route_guard_then_the_mcp_recovery_then_the_desktop_workspace_patch(self):
        manager = WebappComponent.__new__(WebappComponent)
        manager.application_root = ROOT
        patches = manager.webapp_patches()
        self.assertEqual([item.name for item in patches], ['webapp-route-guard.patch', 'webapp-mcp-recovery.patch', 'webapp-desktop-workspace.patch'])
        self.assertTrue(all(item.is_file() for item in patches))

    def test_a_patch_sequence_is_applied_in_order_and_recognised_as_a_whole_or_by_its_first_part(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            for name in ('a.txt', 'b.txt'):
                (repo / name).write_text('one\n')
            subprocess.run([*GIT, '-C', str(repo), 'add', '.'], check=True)
            subprocess.run([*GIT, '-C', str(repo), 'commit', '-qm', 'c'], check=True)
            patches = []
            for name in ('a.txt', 'b.txt'):
                (repo / name).write_text('two\n')
                patch = Path(d) / f'{name}.patch'
                patch.write_bytes(subprocess.run(['git', '-C', str(repo), 'diff', '--', name], capture_output=True, check=True).stdout)
                subprocess.run(['git', '-C', str(repo), 'checkout', '-q', name], check=True)
                patches.append(patch)
            revision = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            pin = types.SimpleNamespace(revision=revision)
            self.assertEqual(apply_patch(repo, pin, patches), 'applied')
            self.assertEqual(apply_patch(repo, pin, patches), 'applied')      # idempotent
            self.assertEqual(patch_state(repo, patches), 'applied')
            self.assertEqual(patch_state(repo, patches[:1]), 'foreign', 'the first patch alone is not the whole applied tree')
            self.assertEqual(patch_state(repo, patches[1:]), 'foreign')
            (repo / 'a.txt').write_text('tampered\n')
            self.assertEqual(patch_state(repo, patches), 'foreign')

    def test_patches_that_touch_the_same_file_are_applied_in_order_and_recognised_as_a_whole(self):
        with tempfile.TemporaryDirectory() as d:
            repo = Path(d) / 'repo'
            repo.mkdir()
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            (repo / 'f.txt').write_text('1\n2\n3\n4\n5\n6\n7\n8\n9\n')
            subprocess.run([*GIT, '-C', str(repo), 'add', '.'], check=True)
            subprocess.run([*GIT, '-C', str(repo), 'commit', '-qm', 'c'], check=True)
            patches = [Path(d) / 'p0.patch', Path(d) / 'p1.patch']
            (repo / 'f.txt').write_text('one\n2\n3\n4\n5\n6\n7\n8\n9\n')
            patches[0].write_bytes(subprocess.run(['git', '-C', str(repo), 'diff', '--', 'f.txt'], capture_output=True, check=True).stdout)
            subprocess.run([*GIT, '-C', str(repo), 'add', 'f.txt'], check=True)
            (repo / 'f.txt').write_text('one\n2\n3\n4\n5\n6\n7\n8\nnine\n')       # the second patch builds on the first: both change f.txt
            patches[1].write_bytes(subprocess.run(['git', '-C', str(repo), 'diff', '--', 'f.txt'], capture_output=True, check=True).stdout)
            subprocess.run([*GIT, '-C', str(repo), 'reset', '-q', '--hard'], check=True)
            revision = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            pin = types.SimpleNamespace(revision=revision)
            self.assertEqual(apply_patch(repo, pin, patches), 'applied')
            self.assertEqual(patch_state(repo, patches), 'applied')
            self.assertEqual(apply_patch(repo, pin, patches), 'applied')                    # idempotent
            (repo / 'f.txt').write_text((repo / 'f.txt').read_text().replace('2', 'two'))
            self.assertEqual(patch_state(repo, patches), 'foreign')
            self.assertFalse([p for p in (repo / '.git').iterdir() if 'patch-check' in p.name], 'the check leaves nothing in the source')

    def test_a_release_that_carries_only_the_route_guard_is_still_accepted_for_rollback(self):
        manager = WebappComponent.__new__(WebappComponent)
        manager.application_root = ROOT
        calls = []
        with mock.patch('src.hermes_webapp_component.patch_state', lambda path, patches: calls.append(patches) or
                        ('applied' if patches == manager.route_guard_patch() else 'foreign')):
            self.assertEqual(manager.applied_patches('x'), manager.route_guard_patch())
            self.assertEqual(len(calls), 3)

    def test_a_release_with_the_route_guard_and_recovery_but_not_the_desktop_patch_is_still_accepted(self):
        manager = WebappComponent.__new__(WebappComponent)
        manager.application_root = ROOT
        with mock.patch('src.hermes_webapp_component.patch_state', lambda path, patches: 'applied' if tuple(patches) == manager.recovery_patches() else 'foreign'):
            self.assertEqual(manager.applied_patches('x'), manager.recovery_patches())

    def test_patch_state_detects_a_tampered_source(self):
        with tempfile.TemporaryDirectory(dir=ROOT / '.release-tests' if (ROOT / '.release-tests').is_dir() else None) as d:
            repo = Path(d)
            patch = repo.parent / 'x.patch'
            subprocess.run(['git', 'init', '-q', str(repo)], check=True)
            (repo / 'f.txt').write_text('a\n')
            subprocess.run([*GIT, '-C', str(repo), 'add', '.'], check=True)
            subprocess.run([*GIT, '-C', str(repo), 'commit', '-qm', 'c'], check=True)
            (repo / 'f.txt').write_text('b\n')
            patch.write_bytes(subprocess.run(['git', '-C', str(repo), 'diff'], capture_output=True, check=True).stdout)
            subprocess.run(['git', '-C', str(repo), 'checkout', '-q', 'f.txt'], check=True)
            revision = subprocess.run(['git', '-C', str(repo), 'rev-parse', 'HEAD'], capture_output=True, text=True).stdout.strip()
            pin = types.SimpleNamespace(revision=revision)
            self.assertEqual(apply_patch(repo, pin, patch), 'applied')
            (repo / 'f.txt').write_text('tampered\n')
            self.assertEqual(patch_state(repo, patch), 'foreign')
            with self.assertRaises(Exception):
                verify_checkout(repo, pin, patch)
            patch.unlink()


class PluginUpgradeTests(unittest.TestCase):
    def component(self, root):
        manager = WebappComponent.__new__(WebappComponent)
        manager.application_root = ROOT
        return manager

    def test_our_earlier_plugin_is_upgraded_and_a_foreign_one_never_overwritten(self):
        from src.hermes_webapp_component import KNOWN_PREVIOUS_PLUGIN_SHA256, PLUGIN_NAME
        shipped = (ROOT / 'integrations/hermes-mode/desktop-plugins' / PLUGIN_NAME / 'plugin.js').read_bytes()
        self.assertTrue(KNOWN_PREVIOUS_PLUGIN_SHA256 and all(len(item) == 64 for item in KNOWN_PREVIOUS_PLUGIN_SHA256))
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home / 'desktop-plugins' / PLUGIN_NAME / 'plugin.js'
            manager = self.component(home)
            manager.install_desktop_plugin(home)
            self.assertEqual(target.read_bytes(), shipped)                                   # fresh install
            target.write_text('// the user wrote this\n')
            with self.assertRaises(Exception):
                manager.install_desktop_plugin(home)                                         # unknown content: refused ...
            self.assertEqual(target.read_text(), '// the user wrote this\n')                # ... and untouched
            import hashlib
            fake_old = b'// pretend earlier shipped copy\n'
            target.write_bytes(fake_old)
            with mock.patch('src.hermes_webapp_component.KNOWN_PREVIOUS_PLUGIN_SHA256', (hashlib.sha256(fake_old).hexdigest(),)):
                manager.install_desktop_plugin(home)
            self.assertEqual(target.read_bytes(), shipped)                                   # a recognised earlier copy of ours is upgraded

    def test_the_plugin_uses_history_only_with_the_arrival_hint_and_always_has_a_fallback(self):
        text = (ROOT / 'integrations/hermes-mode/desktop-plugins/odysseus-return/plugin.js').read_text(encoding='utf-8')
        for needle in ('odysseus_entered', 'window.history.go(-steps)', 'window.location.assign(href)', "hostname !== 'hermes.localhost'", 'pagehide'):
            self.assertIn(needle, text)
        self.assertNotIn('document.referrer', text)
        self.assertIn("headers.get('x-odysseus-grant') === 'required'", text)                              # a revoked grant sends the tab to Odysseus
        self.assertIn('new URL(response.url).origin === window.location.origin', text)                    # and only this page's own origin is watched


class BackupScopeTests(unittest.TestCase):
    def test_webapp_state_is_saved_grants_and_credentials_are_not(self):
        parent = ROOT / '.release-tests'
        parent.mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(dir=parent) as directory:
            base = Path(directory)
            ody, hermes, component = base / 'ody', base / 'hermes', base / 'component'
            (ody / 'data').mkdir(parents=True)
            hermes.mkdir()
            (ody / 'data' / 'settings.json').write_text('{}')
            (ody / 'data' / 'hermes-grants.db').write_bytes(b'not-a-real-database')
            (component / 'managed' / 'webapp').mkdir(parents=True)
            (component / 'managed' / 'webapp' / 'component.json').write_text('{"kind":"webapp"}')
            gateway = component / 'state' / 'hermes-gateway'
            (gateway / 'webapp').mkdir(parents=True)
            (gateway / 'webapp' / 'credential.json').write_text('SECRET')
            (gateway / 'memories').mkdir()
            (gateway / 'memories' / 'm.md').write_text('keep')
            backup = create_backup(ody, hermes, base / 'backups', component_root=component)
            self.assertTrue(verify_backup(backup)['verified'])
            self.assertEqual((backup / 'component/managed/webapp/component.json').read_text(), '{"kind":"webapp"}')
            self.assertEqual((backup / 'component/state/hermes-gateway/memories/m.md').read_text(), 'keep')
            self.assertFalse((backup / 'odysseus/data/hermes-grants.db').exists())
            self.assertFalse(list(backup.rglob('credential.json')))
            self.assertIn('hermes-grants.db', json.dumps(json.loads((backup / 'manifest.json').read_text())['excluded']))


if __name__ == '__main__':
    unittest.main()
