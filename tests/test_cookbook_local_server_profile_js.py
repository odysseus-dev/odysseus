"""Regression guards for the Cookbook local server profile."""

import json
from pathlib import Path
import shutil
import subprocess

import pytest


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


def test_missing_local_active_remote_executes_local_only_transition():
    if shutil.which("node") is None:
        pytest.skip("node binary not on PATH")

    server_helpers = _between(
        COOKBOOK,
        "function _isLocalEntry(s)",
        "const GEMMA4_THINKING_CHAT_TEMPLATE",
    ).replace("export ", "")
    selection = _between(
        COOKBOOK,
        "function _applyServerSelection(val)",
        "async function _refreshScanDownloadTarget()",
    )
    synthesis = _between(
        COOKBOOK,
        "let _localSeen = false;",
        "if (_es.remoteHost &&",
    )
    state = {
        "remoteHost": "gpu.example",
        "remoteServerKey": "srv:remote",
        "env": "venv",
        "envPath": "/remote/venv",
        "platform": "linux",
        "hostPlatform": "windows",
        "servers": [{
            "name": "Remote",
            "host": "gpu.example",
            "env": "venv",
            "envPath": "/remote/venv",
            "platform": "linux",
        }],
    }
    script = f"""
      const _envState = {json.dumps(state)};
      const document = {{ querySelectorAll: () => [] }};
      const _persistEnvState = () => {{}};
      {server_helpers}
      {selection}
      const _es = _envState;
      {synthesis}
      const remoteBefore = JSON.stringify(_envState.servers.find(s => s.host === 'gpu.example'));
      _applyServerSelection('local');
      console.log(JSON.stringify({{
        state: _envState,
        local: _envState.servers.find(_isLocalEntry),
        remoteUnchanged: remoteBefore === JSON.stringify(_envState.servers.find(s => s.host === 'gpu.example')),
      }}));
    """
    proc = subprocess.run(
        ["node", "--input-type=module"],
        input=script,
        capture_output=True,
        text=True,
        timeout=30,
    )
    assert proc.returncode == 0, proc.stderr
    result = json.loads(proc.stdout.strip().splitlines()[-1])

    assert result["local"]["env"] == "none"
    assert result["local"]["envPath"] == ""
    assert result["local"]["platform"] == "windows"
    assert result["state"]["remoteHost"] == ""
    assert result["state"]["remoteServerKey"] == ""
    assert result["state"]["env"] == "none"
    assert result["state"]["envPath"] == ""
    assert result["state"]["platform"] == "windows"
    assert result["remoteUnchanged"] is True
