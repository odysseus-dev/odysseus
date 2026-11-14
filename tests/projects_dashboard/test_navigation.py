import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
HTML = (ROOT / 'static/projects.html').read_text(encoding='utf-8')
JS = (ROOT / 'static/js/projects.js').read_text(encoding='utf-8')


def test_header_has_both_navigation_controls_and_back_to_odysseus_is_unchanged():
    assert re.search(r'<a id="back" href="/"[^>]*>&larr; Odysseus</a>', HTML)
    assert re.search(r'<button type="button" id="toHermes"[^>]*>&larr; Back to Hermes</button>', HTML)


def test_back_to_hermes_uses_the_launch_ticket_never_a_caller_supplied_address():
    assert "$('toHermes')" in JS and 'button.onclick = () => openProjects(id)' in JS
    assert "fetch('/api/application-modes/projects/launch'" in JS
    for name in ('location.href', 'location.assign', 'window.open'):
        assert name not in JS
    assert "url.hostname !== 'hermes.localhost'" in JS and "'/__odysseus/enter'" in JS      # the returned ticket address is still checked before opening
    assert JS.count('location.replace(') == 1 and JS.count('follow(') == 2 and 'follow(url.href)' in JS      # the only navigation is follow(), called only with the checked ticket address
    assert 'from' in JS and "get('from')" in JS and 'RETURN_ID' in JS


def test_the_return_id_pattern_matches_the_server_and_rejects_urls():
    from src.application_modes import RESUME_ID
    pattern = re.search(r'RETURN_ID = /\^(.+)\$/;', JS).group(1)
    assert pattern == RESUME_ID.pattern
    for good in ('c8b9182a1f17', '20260101_ab-c.d'):
        assert re.fullmatch(pattern, good) and RESUME_ID.fullmatch(good)
    for bad in ('http://evil.example/', '//evil.example', '../x', 'a b', 'a/b', '', 'x' * 201, 'javascript:alert(1)'):
        assert not re.fullmatch(pattern, bad) and not RESUME_ID.fullmatch(bad)


PLUGIN = (ROOT / 'integrations/hermes-mode/desktop-plugins/odysseus-resume/plugin.js').read_text(encoding='utf-8')


def test_projects_opens_inside_hermes_as_a_settings_page_and_only_the_diagnostic_link_leaves():
    assert "area: 'sidebar.nav'" in PLUGIN and 'registerSettingsPage' in PLUGIN and "label: 'Projects'" in PLUGIN and 'pluginSettingsHref' in PLUGIN
    assert "area: 'routes'" not in PLUGIN
    assert "hostname !== 'hermes.localhost'" in PLUGIN and 'http://localhost:${port}/static/projects.html?' in PLUGIN      # the diagnostic link is still built from this page's own port
    assert 'focusedStoredSessionId' in PLUGIN and 'VALID.test(active)' in PLUGIN
    assert PLUGIN.count('window.location.assign(') == 1 and 'window.open' not in PLUGIN
    assert "const API = '/__odysseus/api/projects'" in PLUGIN and "credentials: 'same-origin'" in PLUGIN       # same-origin data only
    for forbidden in ('session-token', 'sessionToken', '/api/ws', "method: 'POST'", "method: 'PUT'", "method: 'DELETE'", 'localStorage'):
        assert forbidden not in PLUGIN
    assert 'searchParams.get(' in PLUGIN and 'takeRequestedSession()' in PLUGIN       # resume still reads only its one parameter
    assert "searchParams.get('from')" not in PLUGIN and "get('url')" not in PLUGIN and "get('next')" not in PLUGIN


def test_the_resume_plugin_still_parses_as_a_module():
    import shutil
    import subprocess
    node = shutil.which('node')
    if not node:
        import pytest
        pytest.skip('node not available')
    done = subprocess.run([node, '--input-type=module', '--check'], input=PLUGIN, capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def test_manage_in_hermes_only_opens_hermes_and_adds_no_project_controls_of_its_own():
    assert "'Manage in Hermes'" in JS and "onclick: () => openProjects()" in JS
    assert 'Filters' in JS and 'Move to project' in JS                              # tells the user where the native controls are
    for forbidden in ('projects.create', 'projects.update', 'session.workspace.move', 'session-token', '/api/ws'):
        assert forbidden not in JS


def test_project_management_goes_through_the_webapps_own_gateway_connection_only():
    assert 'host.request(method, params)' in PLUGIN
    used = set(re.findall(r"'((?:projects|session)\.[a-z_.]+)'", PLUGIN))
    assert used == {'projects.list', 'projects.create', 'projects.update', 'projects.add_folder', 'projects.remove_folder', 'projects.set_primary', 'projects.archive', 'session.list', 'session.workspace.move'}
    assert 'projects.delete' not in PLUGIN and 'session.cwd.set' not in PLUGIN and 'grant' not in ' '.join(used)          # no deletion, no live-agent cwd change, no write grants
    assert "BUSY_STATES = new Set(['working', 'waiting'])" in PLUGIN and 'row.running' in PLUGIN                           # a running conversation is never moved
    assert 'Write permissions do not change' in PLUGIN
