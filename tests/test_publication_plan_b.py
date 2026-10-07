"""Publication behavior: cold catalogs, server QR, notices, and removed resources."""
import asyncio
import base64
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_retained_artifacts_match_distributed_provenance_and_notices():
    ledger = json.loads((ROOT / 'THIRD_PARTY_PROVENANCE.json').read_text())
    assert len(ledger['retained']) == 6
    for entry in ledger['retained']:
        assert entry['source_records']
        assert hashlib.sha256((ROOT / entry['notice']).read_bytes()).hexdigest() == entry['notice_sha256']
        for artifact in entry['artifacts']:
            data = (ROOT / artifact['path']).read_bytes()
            assert len(data) == artifact['size_bytes']
            assert hashlib.sha256(data).hexdigest() == artifact['sha256']


def test_removed_resource_ledger_has_no_runtime_references():
    ledger = (ROOT / 'PUBLICATION_ASSET_DECISIONS.md').read_text()
    removed = re.findall(r'^- SAN-\d+: `([^`]+)`$', ledger, re.M)
    assert len(removed) == 21
    # Inspect tracked text, including files omitted by parity-audit exclusions.
    tracked = subprocess.check_output(['git', 'ls-files', '-z'], cwd=ROOT).decode().split('\0')
    for path in removed:
        assert not (ROOT / path).exists()
        for name in tracked:
            if name == "PUBLICATION_ASSET_DECISIONS.md":
                continue  # Historical ledger entries are intentionally non-resource references.
            file = ROOT / name
            if not file.is_file():
                continue
            try:
                content = file.read_text()
            except UnicodeError:
                continue
            assert Path(path).name not in content, (path, name)


def test_cold_catalog_and_refresh_use_mutable_data(monkeypatch, tmp_path):
    from services.hwfit import models, hf_discovery as discovery
    from src import constants

    monkeypatch.setattr(constants, 'DATA_DIR', str(tmp_path))
    monkeypatch.setattr(models, '_models_cache', None)
    monkeypatch.setattr(discovery, 'MLX_COMMUNITY_CACHE', tmp_path / 'hwfit/mlx_community_models.json')
    monkeypatch.setattr(discovery, 'HF_COLLECTION_MODELS_CACHE', tmp_path / 'hwfit/hf_collection_models.json')
    for file in ['hf_models.json', 'mlx_community_models.json']:
        assert (ROOT / 'services/hwfit/data' / file).read_text() == '[]\n'
    assert models.get_models() == []
    row = {'name': 'test/runtime', 'parameter_count': '3B', 'quantization': 'F16'}
    monkeypatch.setattr(discovery, 'fetch_collection_models', lambda source: [dict(row, name='mlx-community/test' if source['mlx_only'] else row['name'])] if source.get('mlx_only') else [row])
    assert models.refresh_dynamic_catalogs(force=True) == {'mlx_community': 1, 'hf_collections': 1}
    assert {r['name'] for r in models.get_models()} == {'test/runtime', 'mlx-community/test'}
    cached = json.loads(discovery.HF_COLLECTION_MODELS_CACHE.read_text())
    assert cached['source'] and cached['fetched_at'] and cached['count'] == 1
    # Offline cache loading must work without a fetch.
    models.reset_model_cache()
    monkeypatch.setattr(discovery, 'fetch_collection_models', Mock(side_effect=OSError('offline')))
    assert len(models.get_models()) == 2
    user_file = Path(models.model_catalog_path())
    user_file.write_text(json.dumps([dict(row, name='test/user')]))
    models.reset_model_cache()
    assert len(models.get_models()) == 3


def test_empty_catalog_route_gives_real_refresh_guidance(monkeypatch):
    from services.hwfit import models, hardware
    from routes.hwfit_routes import setup_hwfit_routes

    monkeypatch.setattr(hardware, 'detect_system', lambda **kwargs: {'backend': 'cpu_x86'})
    monkeypatch.setattr(models, 'get_models', lambda: [])
    monkeypatch.setattr(models, 'refresh_dynamic_catalogs', Mock(side_effect=OSError('offline')))
    endpoint = next(r.endpoint for r in setup_hwfit_routes().routes if r.path.endswith('/models'))
    result = endpoint(refresh_catalog=True)
    assert result['models'] == []
    assert 'Rescan' in result['error'] and 'offline' in result['error']
    assert result['catalog_refresh'] == {'error': 'offline'}


def test_2fa_setup_still_generates_a_server_png():
    from routes.auth_routes import setup_auth_routes

    auth = Mock()
    auth.get_username_for_token.return_value = 'alice'
    auth.totp_enabled.return_value = False
    auth.totp_generate_secret.return_value = 'JBSWY3DPEHPK3PXP'
    auth.totp_get_provisioning_uri.return_value = 'otpauth://totp/test:alice?secret=JBSWY3DPEHPK3PXP&issuer=test'
    endpoint = next(r.endpoint for r in setup_auth_routes(auth).routes if r.path.endswith('/2fa/setup'))
    result = asyncio.run(endpoint(SimpleNamespace(cookies={})))
    assert result['uri'].startswith('otpauth://')
    prefix, data = result['qr_code'].split(',', 1)
    assert prefix == 'data:image/png;base64'
    assert base64.b64decode(data).startswith(b'\x89PNG\r\n\x1a\n')


def test_distribution_paths_include_all_notices():
    for name in ['Odysseus.spec', 'build-windows-portable.ps1', 'build-macos-app.sh', 'Dockerfile']:
        text = (ROOT / name).read_text()
        assert 'licenses' in text and 'THIRD_PARTY_PROVENANCE.json' in text and 'ACKNOWLEDGMENTS.md' in text
    ignore = (ROOT / '.dockerignore').read_text()
    assert '!ACKNOWLEDGMENTS.md' in ignore
    manifest = json.loads((ROOT / 'static/manifest.json').read_text())
    assert not manifest.get('icons')
    assert manifest['display'] == 'standalone'


def test_browser_publication_behaviors():
    """Real Chromium DOM: text-only website, saved fonts, escaped print/math."""
    if not shutil.which('node'):
        pytest.skip('Node is required')
    probe = subprocess.run(['node', '-e', "require.resolve('playwright')"], cwd=ROOT, capture_output=True)
    if probe.returncode:
        pytest.skip('Playwright is required')
    result = subprocess.run(['node', str(ROOT / 'tests/publication_plan_b_browser.cjs')], cwd=ROOT, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stdout + result.stderr
    output = json.loads(result.stdout)
    assert output == {'website': True, 'fonts': True, 'print': True, 'pwa': True}


def test_service_worker_activation_purges_previous_asset_cache():
    result = subprocess.run(['node', '-e', r"""
const fs = require('fs');
const vm = require('vm');
const handlers = {};
const removed = [];
let pending;
const source = fs.readFileSync('static/sw.js', 'utf8');
const current = source.match(/const CACHE_NAME = '([^']+)'/)[1];
vm.runInNewContext(source, {
  self: { addEventListener: (event, callback) => handlers[event] = callback, clients: { claim: () => Promise.resolve() } },
  caches: { keys: async () => ['previous-cache', current], delete: async key => { removed.push(key); return true; } },
});
handlers.activate({ waitUntil: promise => pending = promise });
pending.then(() => process.stdout.write(JSON.stringify(removed))).catch(error => { console.error(error); process.exit(1); });
"""], cwd=ROOT, text=True, capture_output=True, timeout=10)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout) == ['previous-cache']
