"""Guards the standalone GPU compose files against drift.

Stack-management UIs (Portainer, Coolify, Dockhand, ...) often accept only a
single compose file and do not honor COMPOSE_FILE or multiple ``-f`` overlays,
so the repo ships standalone ``docker-compose.gpu-*.yml`` files that inline the
GPU overlay. The base ``docker-compose.yml`` plus ``docker/gpu.*.yml`` overlays
remain the source of truth; these tests assert each standalone file equals the
base compose with only the matching overlay merged into the ``odysseus``
service. No Docker / docker compose is required — everything is pure YAML.
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


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _deep_merge(base: dict, overlay: dict) -> dict:
    """Mirror docker compose overlay semantics for the keys these files use.

    Mappings merge recursively; list-valued service fields are concatenated
    (compose appends override sequences such as ``environment`` rather than
    replacing them); scalars are overwritten. The overlays here only append to
    ``environment`` and add otherwise-absent keys (``deploy``, ``devices``,
    ``group_add``), so this keeps the expected merge explicit without invoking
    docker compose.
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
    """Build the expected standalone config: base + overlay on odysseus only."""
    expected = copy.deepcopy(base)
    overlay_service = overlay["services"][SERVICE]
    expected["services"][SERVICE] = _deep_merge(
        expected["services"][SERVICE], overlay_service
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
def test_non_odysseus_services_match_base(base, standalone_path):
    standalone = _load(standalone_path)
    for name, definition in base["services"].items():
        if name == SERVICE:
            continue
        assert standalone["services"][name] == definition
    assert set(standalone["services"]) == set(base["services"])


@pytest.mark.parametrize("standalone_path", [NVIDIA_STANDALONE, AMD_STANDALONE])
def test_top_level_volumes_match_base(base, standalone_path):
    standalone = _load(standalone_path)
    assert standalone.get("volumes") == base.get("volumes")


# --- odysseus = base service + only the overlay additions ------------------


def test_nvidia_odysseus_adds_only_overlay(base):
    standalone = _load(NVIDIA_STANDALONE)
    svc = standalone["services"][SERVICE]
    base_svc = base["services"][SERVICE]

    # Base environment preserved, plus exactly the two NVIDIA variables.
    assert "NVIDIA_VISIBLE_DEVICES=all" in svc["environment"]
    assert "NVIDIA_DRIVER_CAPABILITIES=compute,utility" in svc["environment"]
    added_env = set(svc["environment"]) - set(base_svc["environment"])
    assert added_env == {
        "NVIDIA_VISIBLE_DEVICES=all",
        "NVIDIA_DRIVER_CAPABILITIES=compute,utility",
    }

    # deploy block is new and matches the overlay's GPU reservation exactly.
    assert "deploy" not in base_svc
    devices = svc["deploy"]["resources"]["reservations"]["devices"]
    assert devices == [
        {"driver": "nvidia", "count": "all", "capabilities": ["gpu"]}
    ]

    # No AMD-only keys leaked in.
    assert "devices" not in svc
    assert "group_add" not in svc


def test_amd_odysseus_adds_only_overlay(base):
    standalone = _load(AMD_STANDALONE)
    svc = standalone["services"][SERVICE]
    base_svc = base["services"][SERVICE]

    # Environment is unchanged from base for AMD.
    assert svc["environment"] == base_svc["environment"]

    # devices and group_add are new and match the overlay exactly.
    assert "devices" not in base_svc
    assert "group_add" not in base_svc
    assert svc["devices"] == ["/dev/kfd", "/dev/dri"]
    assert svc["group_add"] == ["video", "${RENDER_GID:-render}"]

    # No NVIDIA-only keys leaked in.
    assert "deploy" not in svc
