"""Regression guard for issue #6132 — the ChromaDB named volume was mounted at a
path the image never writes to, so the whole vector store lived in the
container's writable layer and was destroyed on every recreate. Silently: the
collection survived with a count of 0, and retrieval just stopped returning
anything.

``chromadb/chroma`` runs ``chroma run /config.yaml``, and that config is a single
line — ``persist_path: "/data"``. The image declares no ``VOLUME``, so nothing
outside a mount survives. ``/chroma/chroma`` is where pre-1.0 images kept the
store.

The documented backup procedure (website/backup-restore.md) archives the
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
    """Return a normalised list of ``source:target`` strings for the chromadb service.

    Docker Compose accepts volumes in two syntaxes:

    * Short-form string: ``"chromadb-data:/data"`` or ``"chromadb-data:/data:ro"``
    * Long-form mapping::

        - source: chromadb-data
          target: /data
          type: volume

    Both forms are normalised to ``"<source>:<target>"`` so that callers can
    use ``split(":")`` to extract either part without worrying about the original
    syntax or trailing mount-option fields (e.g. ``:z``, ``:ro``).
    """
    compose = yaml.safe_load(path.read_text(encoding="utf-8"))
    service = compose["services"]["chromadb"]
    raw = list(service.get("volumes") or [])
    result: list[str] = []
    for entry in raw:
        if isinstance(entry, dict):
            # Long-form: normalise to "source:target" for uniform handling.
            source = entry.get("source", "")
            target = entry.get("target", "")
            result.append(f"{source}:{target}")
        else:
            # Short-form string: keep as-is; split(":")[1] is still the target
            # even when a third field (mount options like :z, :ro) is present.
            result.append(str(entry))
    return result


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
    """Verify that no mount places chromadb-data at the pre-1.0 store path.

    Uses ``split(":")[1]`` rather than ``endswith()`` so that mount-option
    suffixes (e.g. ``:z``, ``:ro``) do not mask a wrong target path.
    """
    mounts = _chromadb_volumes(path)
    legacy = [m for m in mounts if ":" in m and m.split(":")[1] == LEGACY_PERSIST_PATH]
    assert not legacy, (
        f"{path.name}: {LEGACY_PERSIST_PATH} is the pre-1.0 store location; "
        f"current images never write there. Offending entries: {legacy}"
    )


@pytest.mark.parametrize("path", COMPOSE_FILES, ids=lambda p: p.name)
def test_chromadb_data_volume_is_declared(path: Path):
    compose = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert VOLUME_NAME in (compose.get("volumes") or {}), (
        f"{path.name}: the {VOLUME_NAME} volume is mounted but not declared"
    )


# ---------------------------------------------------------------------------
# Unit tests for _chromadb_volumes() — parse-correctness, not Compose state
# ---------------------------------------------------------------------------

class _FakePath:
    """Minimal Path stand-in that returns a fixed YAML string."""

    def __init__(self, yaml_text: str):
        self._text = yaml_text

    def read_text(self, encoding: str = "utf-8") -> str:  # noqa: ARG002
        return self._text


def test_helper_normalises_long_form_volume_dict():
    """Long-form dict volume entries must not raise AttributeError."""
    p = _FakePath(yaml.dump({
        "services": {"chromadb": {"volumes": [
            {"source": "chromadb-data", "target": "/data", "type": "volume"},
        ]}},
        "volumes": {"chromadb-data": None},
    }))
    result = _chromadb_volumes(p)  # type: ignore[arg-type]
    assert result == ["chromadb-data:/data"]


def test_helper_passes_short_form_string_unchanged():
    """Short-form strings (with or without mount options) are returned as-is."""
    p = _FakePath(yaml.dump({
        "services": {"chromadb": {"volumes": ["chromadb-data:/data:z"]}},
        "volumes": {"chromadb-data": None},
    }))
    result = _chromadb_volumes(p)  # type: ignore[arg-type]
    assert result == ["chromadb-data:/data:z"]
    # split(":")[1] still extracts the correct target regardless of :z suffix
    assert result[0].split(":")[1] == "/data"


def test_helper_legacy_check_catches_path_with_mount_option_suffix():
    """The split-based legacy check catches the old path even with :z appended."""
    mount = f"{VOLUME_NAME}:{LEGACY_PERSIST_PATH}:z"
    # Confirm endswith() would miss it (the bug we fixed)
    assert not mount.endswith(f":{LEGACY_PERSIST_PATH}"), (
        "endswith() should NOT catch ':z'-suffixed legacy path — that was the bug"
    )
    # Confirm split()[1] does catch it
    assert mount.split(":")[1] == LEGACY_PERSIST_PATH, (
        "split(':')[1] must still identify the legacy target path with :z suffix"
    )


# ---------------------------------------------------------------------------
# Regression tests for ChromaDB migration safety invariants (PR #6166 audit)
# ---------------------------------------------------------------------------

MIGRATION_DOC = ROOT / "docs" / "chromadb-volume-migration.md"


def test_migration_guide_requires_quiescence_before_backup():
    """Step 2 must stop ChromaDB before running docker cp to prevent torn SQLite pages."""
    text = MIGRATION_DOC.read_text(encoding="utf-8")
    assert "docker compose stop chromadb" in text, (
        "Migration guide must require stopping ChromaDB before database backup"
    )
    # Confirm stopping step occurs prior to the cold backup step
    stop_step = text.find("Step 2: Quiesce Writers and Gracefully Stop ChromaDB")
    backup_step = text.find("Step 4: Take a Cold File Backup")
    assert stop_step != -1 and backup_step != -1 and stop_step < backup_step, (
        "Quiescence/stop step must precede cold backup step in migration sequence"
    )


def test_migration_guide_mandates_container_preservation_via_commit():
    """Guide must preserve stopped container as an immutable image before any recreation."""
    text = MIGRATION_DOC.read_text(encoding="utf-8")
    assert "docker commit" in text, (
        "Migration guide must instruct operators to commit the container to an immutable backup image"
    )
    assert "com.docker.compose.project" in text, (
        "Guide must explain that Compose discovers containers by internal labels, not name alone"
    )


def test_migration_guide_requires_sqlite_and_hnsw_integrity_verification():
    """Guide must mandate SQLite PRAGMA integrity_check and checking HNSW index files."""
    text = MIGRATION_DOC.read_text(encoding="utf-8")
    assert "PRAGMA integrity_check;" in text, (
        "Migration guide must require SQLite PRAGMA integrity_check"
    )
    assert "data_level0.bin" in text or "*.bin" in text, (
        "Migration guide must require verifying HNSW segment index binary files"
    )


def test_migration_guide_requires_empty_volume_precheck():
    """Guide must verify destination volume is strictly empty prior to seeding."""
    text = MIGRATION_DOC.read_text(encoding="utf-8")
    assert "ls -A" in text or "empty and ready for seeding" in text, (
        "Migration guide must check that the destination named volume is empty before seeding"
    )


def test_migration_guide_includes_staging_validation():
    """Guide must validate the seeded volume with a staging container before Compose cutover."""
    text = MIGRATION_DOC.read_text(encoding="utf-8")
    assert "chromadb-staging" in text, (
        "Migration guide must include staging container validation step"
    )


def test_migration_guide_warns_against_reverting_to_broken_mount():
    """Rollback instructions must explicitly prohibit git checkout to pre-#6166 broken mount."""
    text = MIGRATION_DOC.read_text(encoding="utf-8")
    assert "Do not `git checkout` the pre-#6166 Compose file" in text or "broken" in text.lower(), (
        "Rollback instructions must explain why checking out old Compose file breaks volume mount"
    )
