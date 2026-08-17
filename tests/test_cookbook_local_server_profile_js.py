"""Regression guards for the Cookbook local server profile."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COOKBOOK = (ROOT / "static/js/cookbook.js").read_text(encoding="utf-8")
HWFIT = (ROOT / "static/js/cookbook-hwfit.js").read_text(encoding="utf-8")
DOWNLOAD = (ROOT / "static/js/cookbookDownload.js").read_text(encoding="utf-8")


def _between(source: str, start: str, end: str) -> str:
    start_idx = source.index(start)
    end_idx = source.index(end, start_idx)
    return source[start_idx:end_idx]


def test_local_dropdown_value_resolves_to_the_saved_server_profile():
    resolver = _between(COOKBOOK, "export function _serverByVal(val)", "export function _selectedServer()")

    assert "if (raw === 'local' || raw === '')" in resolver
    assert "_envState.servers.find(_isLocalEntry)" in resolver
    assert "val === 'local' || val === ''" not in resolver.split("const raw", 1)[0]


def test_selecting_local_hydrates_env_path_instead_of_clearing_it():
    selection = _between(COOKBOOK, "function _applyServerSelection(val)", "async function _refreshScanDownloadTarget()")
    scan_selection = _between(HWFIT, "function _syncHostFromScanDropdown()", "// Minimum backend version")
    dropdown_selection = _between(HWFIT, "// Server selector dropdown", "  _syncServerSelectColors();")

    for source in (selection, scan_selection, dropdown_selection):
        assert "_serverByVal('local')" in source
        assert ".env || 'none'" in source
        assert ".envPath || ''" in source

    local_branch = selection.split("if (val === 'local')", 1)[1].split("} else", 1)[0]
    assert "_envState.env = 'none'" not in local_branch
    assert "_envState.envPath = ''" not in local_branch


def test_settings_sync_persists_local_as_the_active_profile():
    cookbook_sync = _between(COOKBOOK, "// Sync server form DOM", "// Wire server form inputs")
    hwfit_sync = _between(HWFIT, "// Servers — sync changes", "async function _testServerConnection")

    assert ": _serverByVal('local')" in cookbook_sync
    assert "_envState.envPath = activeSrv.envPath || ''" in cookbook_sync
    assert "remotes.length === 1" not in cookbook_sync
    assert "_envState.remoteHost || 'local'" in hwfit_sync
    assert "_envState.envPath = sel.envPath || ''" in hwfit_sync


def test_direct_download_uses_the_visible_local_profile():
    direct = _between(COOKBOOK, "const triggerDownload = async () =>", "dlBtn.addEventListener('click', triggerDownload)")

    assert "const _hsrv = _serverByVal(srvVal) || {}" in direct
    assert "let env = _hsrv.env || 'none'" in direct
    assert "const envPath = _hsrv.envPath || ''" in direct
    assert "const srvPlatform = _hsrv.platform || _getPlatform(host || 'local')" in direct
    assert "payload.env_prefix" in direct
    assert "host ? (_hsrv.env" not in direct


def test_model_download_resolves_local_profile_before_building_payload():
    model_download = _between(DOWNLOAD, "export async function _runModelDownload", "const payload = { repo_id: repo, backend }")

    assert "_serverByVal?.('local')" in model_download
    assert "_serverByVal?.(host || 'local')" in model_download
    assert "let env = srv.env || 'none'" in model_download
    assert "const envPath = srv.envPath || ''" in model_download
    assert "host ? (srv.env" not in model_download
