"""Regression guard for issue #6132 — the ChromaDB named volume was mounted at a
path the image never writes to, so the whole vector store lived in the
container's writable layer and was destroyed on every recreate. Silently: the
collection survived with a count of 0, and retrieval just stopped returning
anything.

``chromadb/chroma`` runs ``chroma run /config.yaml``, and that config is a single
line — ``persist_path: "/data"``. The image declares no ``VOLUME``, so nothing
outside a mount survives. ``/chroma/chroma`` is where pre-1.0 images kept the
store.

The documented backup procedure (docs/backup-restore.md) archives the
``chromadb-data`` volume, so a wrong mount point also makes that tarball empty.

Pure YAML — no Docker required.
"""

from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

COMPOSE_FILES = [
    ROOT / "docker-compose.yml",
    ROOT / "docker-compose.gpu-nvidia.yml",
    ROOT / "docker-compose.gpu-amd.yml",
]

# `persist_path` from the image's own /config.yaml.
CHROMA_PERSIST_PATH = "/data"
LEGACY_PERSIST_PATH = "/chroma/chroma"
VOLUME_NAME = "chromadb-data"


def _chromadb_volumes(path: Path) -> list[str]:
    compose = yaml.safe_load(path.read_text(encoding="utf-8"))
    service = compose["services"]["chromadb"]
    return list(service.get("volumes") or [])


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_chromadb_volume_is_mounted_at_the_persist_path(path: Path):
    mounts = _chromadb_volumes(path)
    targets = {m.split(":")[1] for m in mounts if m.startswith(f"{VOLUME_NAME}:")}
    assert targets, f"{path.name}: no {VOLUME_NAME} mount on the chromadb service"
    assert targets == {CHROMA_PERSIST_PATH}, (
        f"{path.name}: {VOLUME_NAME} is mounted at {sorted(targets)}, but the image "
        f"persists to {CHROMA_PERSIST_PATH!r}. Anything written outside a mount is "
        "lost when the container is recreated (issue #6132)."
    )


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_chromadb_does_not_use_the_pre_1_0_path(path: Path):
    mounts = _chromadb_volumes(path)
    assert not any(m.endswith(f":{LEGACY_PERSIST_PATH}") for m in mounts), (
        f"{path.name}: {LEGACY_PERSIST_PATH} is the pre-1.0 store location; current "
        "images never write there"
    )


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_chromadb_data_volume_is_declared(path: Path):
    compose = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert VOLUME_NAME in (compose.get("volumes") or {}), (
        f"{path.name}: the {VOLUME_NAME} volume is mounted but not declared"
    )
