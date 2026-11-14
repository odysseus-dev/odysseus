import hashlib
import shutil
import sys
import tempfile
from pathlib import Path
from unittest import mock

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from src.hermes_component import ComponentError  # noqa: E402
from src.hermes_webapp_component import PLUGIN_NAME, RESUME_PLUGIN_NAME, WebappComponent  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
SOURCES = ROOT / 'integrations/hermes-mode/desktop-plugins'


def component(app_root=ROOT):
    manager = WebappComponent.__new__(WebappComponent)
    manager.application_root = Path(app_root)
    return manager


def test_fresh_install_places_both_plugins_byte_for_byte_and_is_idempotent(tmp_path):
    manager = component()
    assert manager.install_desktop_plugin(tmp_path) == PLUGIN_NAME
    for name in (PLUGIN_NAME, RESUME_PLUGIN_NAME):
        assert (tmp_path / 'desktop-plugins' / name / 'plugin.js').read_bytes() == (SOURCES / name / 'plugin.js').read_bytes()
    stamps = {n: (tmp_path / 'desktop-plugins' / n / 'plugin.js').stat().st_mtime_ns for n in (PLUGIN_NAME, RESUME_PLUGIN_NAME)}
    with mock.patch('src.hermes_webapp_component.atomic_bytes', side_effect=AssertionError('an up-to-date plugin must not be rewritten')):
        manager.install_desktop_plugin(tmp_path)
    assert stamps == {n: (tmp_path / 'desktop-plugins' / n / 'plugin.js').stat().st_mtime_ns for n in (PLUGIN_NAME, RESUME_PLUGIN_NAME)}
    assert manager.installed_desktop_plugins(tmp_path) == [PLUGIN_NAME, RESUME_PLUGIN_NAME]


def test_existing_production_home_with_only_the_return_plugin_gains_resume_and_keeps_return_untouched(tmp_path):
    ret = tmp_path / 'desktop-plugins' / PLUGIN_NAME / 'plugin.js'
    ret.parent.mkdir(parents=True)
    shutil.copy(SOURCES / PLUGIN_NAME / 'plugin.js', ret)
    before = ret.stat().st_mtime_ns
    component().install_desktop_plugin(tmp_path)
    assert ret.stat().st_mtime_ns == before and (tmp_path / 'desktop-plugins' / RESUME_PLUGIN_NAME / 'plugin.js').is_file()


def test_a_foreign_resume_plugin_is_never_overwritten_and_blocks_before_anything_is_written(tmp_path):
    foreign = tmp_path / 'desktop-plugins' / RESUME_PLUGIN_NAME / 'plugin.js'
    foreign.parent.mkdir(parents=True)
    foreign.write_text('// the user wrote this', encoding='utf-8')
    with pytest.raises(ComponentError):
        component().install_desktop_plugin(tmp_path)
    assert foreign.read_text() == '// the user wrote this' and not (tmp_path / 'desktop-plugins' / PLUGIN_NAME).exists()


def test_a_recognised_earlier_resume_copy_is_upgraded(tmp_path):
    old = b'// pretend earlier shipped resume plugin\n'
    target = tmp_path / 'desktop-plugins' / RESUME_PLUGIN_NAME / 'plugin.js'
    target.parent.mkdir(parents=True)
    target.write_bytes(old)
    with mock.patch('src.hermes_webapp_component.KNOWN_PREVIOUS_RESUME_SHA256', (hashlib.sha256(old).hexdigest(),)):
        component().install_desktop_plugin(tmp_path)
    assert target.read_bytes() == (SOURCES / RESUME_PLUGIN_NAME / 'plugin.js').read_bytes()


def test_an_odysseus_without_the_resume_plugin_installs_only_return(tmp_path):
    app = tmp_path / 'app'
    (app / 'integrations/hermes-mode/desktop-plugins' / PLUGIN_NAME).mkdir(parents=True)
    shutil.copy(SOURCES / PLUGIN_NAME / 'plugin.js', app / 'integrations/hermes-mode/desktop-plugins' / PLUGIN_NAME / 'plugin.js')
    home = tmp_path / 'home'
    manager = component(app)
    manager.install_desktop_plugin(home)
    assert manager.installed_desktop_plugins(home) == [PLUGIN_NAME] and not (home / 'desktop-plugins' / RESUME_PLUGIN_NAME).exists()


def test_an_installed_copy_that_differs_only_in_line_endings_is_current_not_a_conflict(tmp_path):
    """Production's installed return plugin is LF while a Windows checkout of the same file is CRLF; ensure must neither refuse nor rewrite it."""
    ret = tmp_path / 'desktop-plugins' / PLUGIN_NAME / 'plugin.js'
    ret.parent.mkdir(parents=True)
    lf = (SOURCES / PLUGIN_NAME / 'plugin.js').read_bytes().replace(b'\r\n', b'\n')
    ret.write_bytes(lf)
    manager = component()
    manager.install_desktop_plugin(tmp_path)
    assert ret.read_bytes() == lf and manager.installed_desktop_plugins(tmp_path) == [PLUGIN_NAME, RESUME_PLUGIN_NAME]
    crlf_app = tmp_path / 'app'
    folder = crlf_app / 'integrations/hermes-mode/desktop-plugins'
    for name in (PLUGIN_NAME, RESUME_PLUGIN_NAME):
        (folder / name).mkdir(parents=True)
        (folder / name / 'plugin.js').write_bytes((SOURCES / name / 'plugin.js').read_bytes().replace(b'\r\n', b'\n').replace(b'\n', b'\r\n'))
    home2 = tmp_path / 'home2'
    (home2 / 'desktop-plugins' / PLUGIN_NAME).mkdir(parents=True)
    (home2 / 'desktop-plugins' / PLUGIN_NAME / 'plugin.js').write_bytes(lf)
    with mock.patch('src.hermes_webapp_component.atomic_bytes') as write:
        component(crlf_app).install_desktop_plugin(home2)
    assert [call.args[0].parent.name for call in write.call_args_list] == [RESUME_PLUGIN_NAME]       # only the missing one is written


class TestStart:
    def manager(self, tmp_path):
        manager = component()
        manager.directory = tmp_path / 'managed'
        (manager.directory / 'service').mkdir(parents=True)
        manager.last_actions = []
        (tmp_path / 'home').mkdir()
        (tmp_path / 'home' / 'config.yaml').write_text('x', encoding='utf-8')
        config = {'home': str(tmp_path / 'home'), 'python': 'python'}
        return manager, config

    def run_start(self, manager, config):
        with mock.patch.object(WebappComponent, '_require_managed', return_value=config), mock.patch.object(WebappComponent, 'validate'), \
                mock.patch.object(WebappComponent, 'install_workspace_grants_plugin'), mock.patch.object(WebappComponent, 'status', return_value={'state': 'ready'}):
            return manager.start()

    def test_start_installs_the_resume_plugin_without_touching_an_installed_return_plugin(self, tmp_path):
        manager, config = self.manager(tmp_path)
        ret = tmp_path / 'home' / 'desktop-plugins' / PLUGIN_NAME / 'plugin.js'
        ret.parent.mkdir(parents=True)
        lf = (SOURCES / PLUGIN_NAME / 'plugin.js').read_bytes().replace(b'\r\n', b'\n')
        ret.write_bytes(lf)
        assert self.run_start(manager, config) == {'state': 'ready'}
        assert ret.read_bytes() == lf and (tmp_path / 'home' / 'desktop-plugins' / RESUME_PLUGIN_NAME / 'plugin.js').is_file()

    def test_a_foreign_plugin_never_stops_the_gateway_from_starting(self, tmp_path):
        manager, config = self.manager(tmp_path)
        foreign = tmp_path / 'home' / 'desktop-plugins' / RESUME_PLUGIN_NAME / 'plugin.js'
        foreign.parent.mkdir(parents=True)
        foreign.write_text('// theirs', encoding='utf-8')
        assert self.run_start(manager, config) == {'state': 'ready'}
        assert foreign.read_text() == '// theirs' and any('left unchanged' in a for a in manager.last_actions)


def test_the_first_deployed_resume_plugin_digests_are_listed_for_in_place_upgrade():
    from src.hermes_webapp_component import KNOWN_PREVIOUS_RESUME_SHA256
    assert KNOWN_PREVIOUS_RESUME_SHA256[1].startswith('596d5f44b76d') and KNOWN_PREVIOUS_RESUME_SHA256[0].startswith('71c3a1ff23b1')   # sha256 of cdc287ad's plugin, LF and CRLF


def test_the_copy_deployed_with_the_sidebar_entry_can_be_upgraded_in_place():
    """The resume plugin live on 2026-11-13 (d918daf3) was missing from the known list, so a new release refused to upgrade it (found when staging the production release)."""
    import hashlib
    from src.hermes_webapp_component import KNOWN_PREVIOUS_RESUME_SHA256
    assert 'db774b38fd7d' in ''.join(h[:12] for h in KNOWN_PREVIOUS_RESUME_SHA256) and '9e5c02a49f3c' in ''.join(h[:12] for h in KNOWN_PREVIOUS_RESUME_SHA256)
