"""A completed blob's empty retry marker must not keep its model stalled."""
import json
import os
import subprocess
import sys

import pytest

from routes.cookbook_helpers import _cached_model_scan_script
from routes.cookbook_output import HF_CACHE_COMPLETE_PROBE, HF_CACHE_INCOMPLETE_PROBE

_REPO = "fixture/stale-marker"
_STATES = ["complete", "stale", "empty-unpaired", "partial", "partial-paired", "empty-paired-empty"]


@pytest.fixture(params=["custom", "hf-home", "hub-env"])
def cache(request, tmp_path):
    root = tmp_path / "cache with spaces"
    model = root / "hub" / ("models--" + _REPO.replace("/", "--"))
    blobs = model / "blobs"
    blobs.mkdir(parents=True)
    snapshot = model / "snapshots" / "revision"
    snapshot.mkdir(parents=True)
    (snapshot / "config.json").write_text("{}")
    env = {key: value for key, value in os.environ.items()
           if key not in {"HF_HOME", "HUGGINGFACE_HUB_CACHE", "HF_HUB_CACHE"}}
    if request.param == "custom":
        argument = str(root)
        scan = _cached_model_scan_script(add_hf_cache=str(root / "hub"))
    else:
        argument = ""
        env["HF_HOME" if request.param == "hf-home" else "HUGGINGFACE_HUB_CACHE"] = str(
            root if request.param == "hf-home" else root / "hub"
        )
        scan = _cached_model_scan_script()
    return blobs, argument, env, scan


def _seed(blobs, state):
    if state in {"complete", "stale", "partial-paired"}:
        (blobs / "sha").write_bytes(b"completed model data")
    elif state == "empty-paired-empty":
        (blobs / "sha").touch()
    if state != "complete":
        (blobs / "sha.incomplete").write_bytes(b"partial" if state.startswith("partial") else b"")


@pytest.mark.parametrize("state", _STATES)
@pytest.mark.parametrize("probe", [HF_CACHE_COMPLETE_PROBE, HF_CACHE_INCOMPLETE_PROBE], ids=["complete", "incomplete"])
def test_cache_status_probes_distinguish_stale_and_partial(cache, state, probe):
    blobs, argument, env, _ = cache
    _seed(blobs, state)
    before = {path.name: path.read_bytes() for path in blobs.iterdir()}
    result = subprocess.run([sys.executable, "-c", probe, _REPO, argument],
                            env=env, capture_output=True, text=True, timeout=15)
    complete = state in {"complete", "stale"}
    expected = complete if probe == HF_CACHE_COMPLETE_PROBE else not complete
    assert result.returncode == (0 if expected else 1), result.stderr
    assert {path.name: path.read_bytes() for path in blobs.iterdir()} == before


@pytest.mark.parametrize("state", _STATES)
def test_generated_inventory_agrees_without_mutating_cache(cache, state):
    blobs, _, env, scan = cache
    _seed(blobs, state)
    before = {path.name: path.read_bytes() for path in blobs.iterdir()}
    # Inventory's unrelated Ollama discovery stays off the network. Execute
    # the actual generated scanner, including its cache-root resolution.
    harness = (
        "import urllib.request,shutil\n"
        "def offline(*args,**kwargs): raise OSError('Ollama outside this fixture')\n"
        "urllib.request.urlopen=offline\nshutil.which=lambda name: None\n"
        + scan
    )
    result = subprocess.run([sys.executable, "-c", harness], env=env,
                            capture_output=True, text=True, timeout=15)
    assert result.returncode == 0, result.stderr
    row = next(item for item in json.loads(result.stdout) if item["repo_id"] == _REPO)
    assert row["has_incomplete"] == (state not in {"complete", "stale"})
    if state in {"complete", "stale"}:
        assert row["nb_files"] == 1
        assert row["size_bytes"] == len(b"completed model data")
    assert {path.name: path.read_bytes() for path in blobs.iterdir()} == before
