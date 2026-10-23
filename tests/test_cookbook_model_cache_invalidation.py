"""Regression coverage for cached-model invalidation after downloads."""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest


ROOT = Path(__file__).resolve().parents[1]
HAS_NODE = shutil.which("node") is not None


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def _run_node(script: str) -> dict:
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT,
        capture_output=True,
        timeout=15,
        text=True,
    )
    if result.returncode != 0:
        raise AssertionError(f"node failed:\n{result.stderr}")
    return json.loads(result.stdout.strip().splitlines()[-1])


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_cache_helpers_scope_invalidation_to_download_target():
    script = textwrap.dedent("""
        const cache = await import('./static/js/cookbookModelCache.js');
        const entries = {
          local: { ts: 1 },
          '?host=gpu-a&ssh_port=22&model_dir=%2Fmodels': { ts: 2 },
          '?host=gpu-b&ssh_port=22': { ts: 3 },
        };
        const local = cache.invalidateCachedModelScans(entries, '');
        const remote = cache.invalidateCachedModelScans(entries, 'gpu-a');
        console.log(JSON.stringify({
          localKeys: Object.keys(local),
          remoteKeys: Object.keys(remote),
          localMatch: cache.modelDownloadMatchesTarget(
            { host: '', serverKey: '' }, { host: '', serverKey: 'local' }),
          remoteMatch: cache.modelDownloadMatchesTarget(
            { host: 'gpu-a', serverKey: 'srv:a' }, { host: 'gpu-a', serverKey: 'srv:a' }),
          wrongProfile: cache.modelDownloadMatchesTarget(
            { host: 'gpu-a', serverKey: 'srv:a' }, { host: 'gpu-a', serverKey: 'srv:b' }),
        }));
    """)
    assert _run_node(script) == {
        "localKeys": ["?host=gpu-a&ssh_port=22&model_dir=%2Fmodels", "?host=gpu-b&ssh_port=22"],
        "remoteKeys": ["local", "?host=gpu-b&ssh_port=22"],
        "localMatch": True,
        "remoteMatch": True,
        "wrongProfile": False,
    }


@pytest.mark.skipif(not HAS_NODE, reason="node binary not on PATH")
def test_only_model_downloads_emit_cache_completion():
    script = textwrap.dedent("""
        const { isModelDownloadTask, modelDownloadCompletedDetail } =
          await import('./static/js/cookbookModelCache.js');
        const model = {
          type: 'download', remoteHost: 'gpu-a', remoteServerKey: 'srv:a',
          payload: { repo_id: 'org/model' },
        };
        console.log(JSON.stringify({
          model: isModelDownloadTask(model),
          dependency: isModelDownloadTask({
            type: 'download', payload: { repo_id: 'torch', _dep: true },
          }),
          pip: isModelDownloadTask({ type: 'download', payload: { repo_id: 'pip-vllm' } }),
          serve: isModelDownloadTask({ type: 'serve', payload: { repo_id: 'org/model' } }),
          detail: modelDownloadCompletedDetail(model),
        }));
    """)
    assert _run_node(script) == {
        "model": True,
        "dependency": False,
        "pip": False,
        "serve": False,
        "detail": {"repoId": "org/model", "host": "gpu-a", "serverKey": "srv:a"},
    }


def test_completion_transition_notifies_foreground_and_background_paths():
    source = _read("static/js/cookbookRunning.js")

    assert "const completedModelDownload = isModelDownloadTask(task)" in source
    assert "if (completedModelDownload) _notifyModelDownloadCompleted(task);" in source
    assert "const completedModelDownloads = [];" in source
    assert "completedModelDownloads.forEach(t => _notifyModelDownloadCompleted(t));" in source
    assert "document.dispatchEvent(new CustomEvent(MODEL_DOWNLOAD_COMPLETED_EVENT" in source


def test_completion_refreshes_both_cached_model_views():
    serve = _read("static/js/cookbookServe.js")
    hwfit = _read("static/js/cookbook-hwfit.js")

    assert "_invalidateCachedModelScan(detail.host);" in serve
    assert "_fetchCachedModels(true).catch(() => {});" in serve
    assert "fresh ? 'fresh' : 'cached'" in serve
    assert "_cachedModelIds = null;" in hwfit
    assert "_refreshCachedModelIds({ force: true }).catch(() => {});" in hwfit
    assert "document.addEventListener(MODEL_DOWNLOAD_COMPLETED_EVENT" in hwfit
