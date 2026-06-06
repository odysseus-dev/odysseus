"""Guards the standalone GPU compose files against drift.

Stack-management UIs (Portainer, Coolify, Dockhand, ...) often accept only a
single compose file and do not honor COMPOSE_FILE or multiple ``-f`` overlays,
so the repo ships standalone ``docker-compose.gpu-*.yml`` files that inline the
GPU overlay. The base ``docker-compose.yml`` plus ``docker/gpu.*.yml`` overlays
remain the source of truth; these tests assert each standalone file equals the
base compose with the matching overlay merged in. Regenerate standalones with:

    python scripts/sync-gpu-compose-standalone.py

No Docker / docker compose is required — everything is pure YAML.
"""

import copy
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[1]

BASE = ROOT / "docker-compose.yml"
NVIDIA_OVERLAY = ROOT / "docker" / "gpu.nvidia.yml"
AMD_OVERLAY = ROOT / "docker" / "gpu.amd.yml"
NVIDIA_STANDALONE = ROOT / "docker-compose.gpu-nvidia.yml"
AMD_STANDALONE = ROOT / "docker-compose.gpu-amd.yml"

SERVICE = "odysseus"
OLLAMA = "ollama"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Mirror docker compose overlay semantics for the keys these files use.

    Mappings merge recursively; list-valued service fields are concatenated
    (compose appends override sequences such as ``environment`` rather than
    replacing them); scalars are overwritten.
    """
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        elif isinstance(value, list) and isinstance(result.get(key), list):
            result[key] = copy.deepcopy(result[key]) + copy.deepcopy(value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _merge_overlay_into_base(base: dict, overlay: dict) -> dict:
    """Build the expected standalone config: base + overlay on all services."""
    expected = copy.deepcopy(base)
    for name, overlay_svc in overlay.get("services", {}).items():
        if name in expected["services"]:
            expected["services"][name] = _deep_merge(
                expected["services"][name], overlay_svc
            )
        else:
            expected["services"][name] = copy.deepcopy(overlay_svc)
    if overlay.get("volumes"):
        expected["volumes"] = _deep_merge(
            expected.get("volumes", {}), overlay["volumes"]
        )
    return expected


@pytest.fixture(scope="module")
def base():
    return _load(BASE)


# --- Equivalence: standalone == base + overlay -----------------------------


def test_nvidia_standalone_equals_base_plus_overlay(base):
    overlay = _load(NVIDIA_OVERLAY)
    standalone = _load(NVIDIA_STANDALONE)
    assert standalone == _merge_overlay_into_base(base, overlay)


def test_amd_standalone_equals_base_plus_overlay(base):
    overlay = _load(AMD_OVERLAY)
    standalone = _load(AMD_STANDALONE)
    assert standalone == _merge_overlay_into_base(base, overlay)


# --- Non-odysseus services and volumes untouched ---------------------------


@pytest.mark.parametrize("standalone_path", [NVIDIA_STANDALONE, AMD_STANDALONE])
def test_non_odysseus_services_match_base_or_overlay(base, standalone_path):
    overlay_path = (
        NVIDIA_OVERLAY if standalone_path == NVIDIA_STANDALONE else AMD_OVERLAY
    )
    overlay = _load(overlay_path)
    standalone = _load(standalone_path)
    for name, definition in base["services"].items():
        if name == SERVICE:
            continue
        assert standalone["services"][name] == definition
    assert standalone["services"][OLLAMA] == overlay["services"][OLLAMA]
    assert set(standalone["services"]) == set(base["services"]) | {OLLAMA}


@pytest.mark.parametrize("standalone_path", [NVIDIA_STANDALONE, AMD_STANDALONE])
def test_top_level_volumes_include_ollama(base, standalone_path):
    overlay_path = (
        NVIDIA_OVERLAY if standalone_path == NVIDIA_STANDALONE else AMD_OVERLAY
    )
    overlay = _load(overlay_path)
    standalone = _load(standalone_path)
    expected_volumes = _deep_merge(base.get("volumes", {}), overlay.get("volumes", {}))
    assert standalone.get("volumes") == expected_volumes


# --- odysseus = base service + only the overlay additions ------------------


def test_nvidia_odysseus_adds_only_overlay(base):
    overlay = _load(NVIDIA_OVERLAY)
    standalone = _load(NVIDIA_STANDALONE)
    svc = standalone["services"][SERVICE]
    base_svc = base["services"][SERVICE]
    overlay_svc = overlay["services"][SERVICE]

    assert svc["environment"] == base_svc["environment"] + overlay_svc["environment"]
    assert svc["depends_on"] == _deep_merge(
        base_svc["depends_on"], overlay_svc["depends_on"]
    )
    assert "deploy" not in base_svc
    devices = svc["deploy"]["resources"]["reservations"]["devices"]
    assert devices == [
        {"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}
    ]
    assert "devices" not in svc
    assert "group_add" not in svc


def test_amd_odysseus_adds_only_overlay(base):
    overlay = _load(AMD_OVERLAY)
    standalone = _load(AMD_STANDALONE)
    svc = standalone["services"][SERVICE]
    base_svc = base["services"][SERVICE]
    overlay_svc = overlay["services"][SERVICE]

    assert svc["environment"] == base_svc["environment"] + overlay_svc["environment"]
    assert svc["depends_on"] == _deep_merge(
        base_svc["depends_on"], overlay_svc["depends_on"]
    )
    assert "devices" not in base_svc
    assert "group_add" not in base_svc
    assert svc["devices"] == ["/dev/kfd", "/dev/dri"]
    assert svc["group_add"] == ["video", "${RENDER_GID:-render}"]
    assert "deploy" not in svc


def test_nvidia_ollama_has_gpu_reservation():
    overlay = _load(NVIDIA_OVERLAY)
    ollama = overlay["services"][OLLAMA]
    devices = ollama["deploy"]["resources"]["reservations"]["devices"]
    assert devices == [{"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}]


def test_amd_ollama_uses_rocm_image_and_devices():
    overlay = _load(AMD_OVERLAY)
    ollama = overlay["services"][OLLAMA]
    assert ollama["image"] == "docker.io/ollama/ollama:rocm"
    assert ollama["devices"] == ["/dev/kfd", "/dev/dri"]
