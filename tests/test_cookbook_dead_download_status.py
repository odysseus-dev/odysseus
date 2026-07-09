"""Behavioral guards for dead-session download classification (issue #4017).

A download whose tmux pane is gone must not be reported as stopped when its
retained output carries DOWNLOAD_OK, or when the files landed in a custom
download dir. The runner exports HF_HOME=<local_dir>, so the cache lives
under <local_dir>/hub — the probe only finds it if the task's dir is passed
in explicitly rather than read from the probe process's environment.
"""
import os
import subprocess
import sys

from routes.cookbook_output import (
    classify_dead_download,
    HF_CACHE_COMPLETE_PROBE,
    HF_CACHE_INCOMPLETE_PROBE,
)

REPO = "org/some-model-GGUF"


# ── Marker classification ──


def test_download_ok_resolves_completed():
    snap = "Fetching 4 files: 100%|####| 4/4\nDownload complete\n\nDOWNLOAD_OK\n$"
    assert classify_dead_download(snap) == ("completed", False)


def test_download_failed_resolves_error():
    snap = "some progress\n\nDOWNLOAD_FAILED (exit 1 after 3 attempts)"
    assert classify_dead_download(snap) == ("error", False)


def test_download_ok_with_zero_files_resolves_error():
    # A DOWNLOAD_OK from a run that matched no files (bad include/quant
    # pattern) is still a failure — same guard as the live-session branch.
    snap = "Fetching 0 files: 0it [00:00, ?it/s]\n\nDOWNLOAD_OK"
    assert classify_dead_download(snap) == ("error", True)


def test_no_marker_returns_none():
    # Mid-download tail with no terminal marker — caller must fall back to
    # the cache probe.
    assert classify_dead_download("Downloading model.gguf:  42%") is None
    assert classify_dead_download("") is None


def test_ollama_pull_output_resolves_completed():
    snap = "pulling manifest\npulling 8f39d1c3...: 100%\nsuccess\n\nDOWNLOAD_OK"
    assert classify_dead_download(snap) == ("completed", False)


# ── Cache probe scripts ──


def _make_cache(root, repo=REPO, incomplete=False, empty_snapshot=False):
    d = os.path.join(root, "hub", "models--" + repo.replace("/", "--"))
    snap = os.path.join(d, "snapshots", "abc123")
    os.makedirs(snap)
    if not empty_snapshot:
        with open(os.path.join(snap, "model.gguf"), "w") as f:
            f.write("x")
    if incomplete:
        blobs = os.path.join(d, "blobs")
        os.makedirs(blobs)
        with open(os.path.join(blobs, "deadbeef.incomplete"), "w") as f:
            f.write("x")


def _run_probe(probe, repo, cache_root, env=None):
    # Strip the HF cache vars so the probe can't accidentally find a real
    # cache on the machine running the tests.
    full_env = {k: v for k, v in os.environ.items()
                if k not in ("HF_HOME", "HUGGINGFACE_HUB_CACHE", "HF_HUB_CACHE")}
    full_env.update(env or {})
    return subprocess.run(
        [sys.executable, "-c", probe, repo, cache_root],
        env=full_env, capture_output=True, timeout=30,
    ).returncode


def test_complete_probe_finds_custom_dir_cache(tmp_path):
    # Model materialized under <local_dir>/hub — found only via the explicit
    # cache_root argument (issue #4017).
    root = str(tmp_path)
    _make_cache(root)
    assert _run_probe(HF_CACHE_COMPLETE_PROBE, REPO, root) == 0


def test_complete_probe_misses_without_cache_root(tmp_path):
    # Same on-disk layout, but without the cache_root argument the probe
    # falls back to the default cache and misses it.
    _make_cache(str(tmp_path))
    assert _run_probe(HF_CACHE_COMPLETE_PROBE, REPO, "") == 1


def test_complete_probe_rejects_incomplete_blobs(tmp_path):
    root = str(tmp_path)
    _make_cache(root, incomplete=True)
    assert _run_probe(HF_CACHE_COMPLETE_PROBE, REPO, root) == 1


def test_complete_probe_rejects_empty_snapshot(tmp_path):
    root = str(tmp_path)
    _make_cache(root, empty_snapshot=True)
    assert _run_probe(HF_CACHE_COMPLETE_PROBE, REPO, root) == 1


def test_complete_probe_env_fallback_still_works(tmp_path):
    # No custom dir on the task — the probe must keep honoring the standard
    # HF env vars so default-cache downloads classify as before.
    root = str(tmp_path)
    _make_cache(root)
    hub = os.path.join(root, "hub")
    assert _run_probe(HF_CACHE_COMPLETE_PROBE, REPO, "", env={"HUGGINGFACE_HUB_CACHE": hub}) == 0


def test_incomplete_probe_sees_custom_dir_partials(tmp_path):
    root = str(tmp_path)
    _make_cache(root, incomplete=True)
    assert _run_probe(HF_CACHE_INCOMPLETE_PROBE, REPO, root) == 0
    # Clean cache → no resumable partials.
    assert _run_probe(HF_CACHE_INCOMPLETE_PROBE, "org/other-model", root) == 1
