"""Cookbook: hardware is detected and a fresh install says how to get a catalog.

No model catalog ships with the app: the bundled lists are empty, and
rows only arrive when Rescan sends `refresh_catalog` while online. So a
fresh data dir that was never refreshed must answer a recommendation
request with an explicit empty-catalog message, not an empty table or a
server error. Ranking itself is covered by the unit tests against an
authored fixture catalog. Downloading and serving a model is left to the
gap list: it needs tmux, a GPU runtime and several gigabytes over the
network.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from src.constants import DATA_DIR

SYSTEM_PATH = "/api/hwfit/system"
MODELS_PATH = "/api/hwfit/models"
STATE_PATH = "/api/cookbook/state"
GPUS_PATH = "/api/cookbook/gpus"

STATE_MARKER = "odysseusSmokeMarker"

# Everything that can give the instance a catalog: the user catalog and the
# two caches a Rescan writes, all under the instance's data dir.
CATALOG_FILES = ("hf_models.json", "hf_collection_models.json", "mlx_community_models.json")


def test_hardware_is_detected(client):
    response = client.get(SYSTEM_PATH)
    assert response.status_code == 200, response.text
    system = response.json()
    assert (system.get("total_ram_gb") or 0) > 0, system
    assert (system.get("cpu_cores") or 0) > 0, system
    assert system.get("cpu_name"), system

    gpus = client.get(GPUS_PATH)
    assert gpus.status_code == 200, gpus.text
    assert gpus.json().get("ok") is True, gpus.text


def test_fresh_install_explains_how_to_populate_the_catalog(client):
    catalog_dir = Path(DATA_DIR) / "hwfit"
    present = [name for name in CATALOG_FILES if (catalog_dir / name).exists()]
    if present:
        pytest.skip(f"not a fresh install: {catalog_dir} already holds {present}")

    response = client.get(MODELS_PATH)
    assert response.status_code == 200, response.text
    body = response.json()
    system = body.get("system") or {}
    assert system.get("cpu_name"), body
    assert body.get("models") == [], body
    error = body.get("error") or ""
    assert "Model catalog is empty" in error, body
    assert "Rescan" in error, body
    # A plain request never refreshes, so nothing was fetched behind our back.
    assert "catalog_refresh" not in body, body
    assert not any((catalog_dir / name).exists() for name in CATALOG_FILES)


def test_cookbook_state_persists(client):
    written = client.post(STATE_PATH, json={STATE_MARKER: "ody-95"})
    assert written.status_code == 200, written.text
    assert written.json().get("ok") is True, written.text

    read = client.get(STATE_PATH)
    assert read.status_code == 200, read.text
    assert read.json().get(STATE_MARKER) == "ody-95", read.text

    client.post(STATE_PATH, json={})
