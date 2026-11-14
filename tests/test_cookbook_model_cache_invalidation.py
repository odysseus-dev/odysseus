"""A finished download must refresh the model list without a manual reload.

Adopted from upstream PR #6640: one DOM event connects the task poller (which
notices completion) to the two cached-model views — the Launch list's scan cache
and the HW-fit "downloaded" markers — instead of waiting for the 6h
localStorage TTL or a hardware rescan.
"""
import json
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HAS_NODE = shutil.which("node") is not None


def _read(relative_path: str) -> str:
    return (ROOT / relative_path).read_text(encoding="utf-8")


def test_event_module_exposes_the_invalidation_contract():
    source = _read("static/js/cookbookModelCache.js")

    assert "export const MODEL_DOWNLOAD_COMPLETED_EVENT" in source
    assert "export function invalidateCachedModelScans" in source
    assert "export function modelDownloadMatchesTarget" in source
    assert "export function isModelDownloadTask" in source


def test_invalidation_drops_only_the_affected_host():
    script = """
import { invalidateCachedModelScans, cachedModelScanHost, modelDownloadMatchesTarget }
  from './static/js/cookbookModelCache.js';
const entries = {
  '?host=alpha': { ts: 1, data: {} },
  '?host=beta': { ts: 2, data: {} },
  'local': { ts: 3, data: {} },
};
const next = invalidateCachedModelScans(entries, 'alpha');
console.log(JSON.stringify({
  keys: Object.keys(next).sort(),
  betaHost: cachedModelScanHost('?host=beta'),
  localHost: cachedModelScanHost('local'),
  sameTarget: modelDownloadMatchesTarget({ host: 'alpha' }, { host: 'alpha', serverKey: 'local' }),
  otherTarget: modelDownloadMatchesTarget({ host: 'beta' }, { host: 'alpha', serverKey: 'local' }),
}));
"""
    result = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=ROOT, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    out = json.loads(result.stdout.strip().splitlines()[-1])
    assert out["keys"] == ["?host=beta", "local"]
    assert out["betaHost"] == "beta"
    assert out["localHost"] == ""
    assert out["sameTarget"] is True
    assert out["otherTarget"] is False


def test_poller_notifies_on_completion():
    source = _read("static/js/cookbookRunning.js")

    assert "MODEL_DOWNLOAD_COMPLETED_EVENT" in source
    assert "function _notifyModelDownloadCompleted(task)" in source
    assert "completedModelDownloads.forEach(t => _notifyModelDownloadCompleted(t));" in source
    # Only a real model download counts — dependency installs are a different path.
    assert "isModelDownloadTask(task)" in source


def test_launch_list_invalidates_and_refetches():
    source = _read("static/js/cookbookServe.js")

    assert "invalidateCachedModelScans" in source
    assert "function _invalidateCachedModelScan(host)" in source
    assert "function _onModelDownloadCompleted(event)" in source
    assert "_fetchCachedModels(true)" in source
    # A forced refresh must not be deduped against an in-flight cached fetch.
    assert "fresh ? 'fresh' : 'cached'" in source


def test_hwfit_markers_refresh_on_completion():
    source = _read("static/js/cookbook-hwfit.js")

    assert "export async function _refreshCachedModelIds" in source
    assert "MODEL_DOWNLOAD_COMPLETED_EVENT" in source
    assert "_refreshCachedModelIds({ force: true })" in source
    assert "const fetchToken = ++_cachedModelIdsFetchToken;" in source


def test_queued_placeholders_are_not_polled_as_sessions():
    """Upstream #7204: a `queue-` placeholder has no backend session yet."""
    source = _read("static/js/cookbookRunning.js")

    assert "function _isQueuedDownloadPlaceholder(task)" in source
    assert "if (!el || !task || _isQueuedDownloadPlaceholder(task)) return;" in source
    assert "if (_isQueuedDownloadPlaceholder(task)) continue;" in source
