"""Regression guards for same-host Cookbook SSH server profiles (#3337)."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
COOKBOOK = (ROOT / "static/js/cookbook.js").read_text(encoding="utf-8")
HWFIT = (ROOT / "static/js/cookbook-hwfit.js").read_text(encoding="utf-8")
DOWNLOAD = (ROOT / "static/js/cookbookDownload.js").read_text(encoding="utf-8")
SERVE = (ROOT / "static/js/cookbookServe.js").read_text(encoding="utf-8")
RUNNING = (ROOT / "static/js/cookbookRunning.js").read_text(encoding="utf-8")


def test_server_dropdown_options_use_profile_keys_not_hosts():
    assert "remoteServerKey" in COOKBOOK
    assert "export function _serverKey(s)" in COOKBOOK
    assert "s?.name || ''" in COOKBOOK
    assert "s?.host || ''" in COOKBOOK
    assert "s?.port || ''" in COOKBOOK
    assert "s?.envPath || ''" in COOKBOOK
    assert 'const value = _serverKey(s);' in COOKBOOK
    assert 'option value="${esc(s.host)}"' not in COOKBOOK


def test_selected_server_helpers_prefer_profile_key_before_host_fallback():
    assert "_envState.remoteServerKey = _serverKey(s);" in COOKBOOK
    assert "const selected = hostOrTask === _envState.remoteHost ? _selectedServer() : null;" in COOKBOOK
    assert "const srv = selected || _serverByVal(hostOrTask);" in COOKBOOK
    assert "const _want = _currentServerValue();" in COOKBOOK


def test_cookbook_submodules_resolve_visible_profile_selection():
    assert "_serverByVal?.(_ssv)" in DOWNLOAD
    assert "_serverByVal?.(_envState.remoteServerKey || host)" in DOWNLOAD
    assert "_serverByVal?.(_envState.remoteServerKey || _zh)" in DOWNLOAD
    assert "_serverByVal(_envState.remoteServerKey || remoteHost)" in HWFIT
    assert "hk: _currentServerValue()" in HWFIT
    assert "sel.value = _currentServerValue();" in HWFIT
    assert "_serverByVal?.(select.value)" in SERVE
    assert "_serverByVal?.(val)" in SERVE
    assert "_serverByVal?.(_es.remoteServerKey || _es.remoteHost || '')" in SERVE
    assert "port: host ? (server?.port || _getPort(host) || '') : ''" in SERVE


def test_serve_launch_preflights_use_selected_target_and_port():
    launch_target = "const launchTarget = _selectedServeTarget(panel);"
    assert launch_target in SERVE
    assert "const _hostStr = launchTarget.host || '';" in SERVE
    assert "const _probeHost = (launchTarget.host || '').trim();" in SERVE
    assert "if (launchTarget.port) _probeParams.set('ssh_port', launchTarget.port);" in SERVE
    assert "const _portHost = (launchTarget.host || '').trim();" in SERVE
    assert "StrictHostKeyChecking=no ${_sshPrefix(launchTarget.port)}${_portHost}" in SERVE
    assert "let serveHost = launchTarget.host || '';" in SERVE
    assert SERVE.index(launch_target) < SERVE.index("const _runningMod = await import('./cookbookRunning.js');")


def test_running_tab_resolves_profile_key_not_first_host():
    assert "_serverByVal(_envState.remoteServerKey || _tHost)" in RUNNING
    assert "_serverByVal(_envState.remoteServerKey || _host)" in RUNNING
    assert "_serverByVal(_envState.remoteServerKey || host)" in RUNNING
    assert "_serverByVal = shared._serverByVal;" in RUNNING
    assert "_selectedServer = shared._selectedServer;" in RUNNING


def test_no_same_host_selector_paths_resolve_by_first_matching_host():
    forbidden = [
        "servers.find(s => s.host === select.value)",
        "servers.find(s => s.host === _ssEl.value)",
        "servers.find(x => x.host === val)",
        "servers.find(s => s.host === _ssv)",
    ]
    combined = "\n".join([DOWNLOAD, HWFIT, SERVE])
    for needle in forbidden:
        assert needle not in combined
