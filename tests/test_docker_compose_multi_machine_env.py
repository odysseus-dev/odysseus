"""Docker Compose must forward multi-machine env vars into the odysseus container."""

from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]

COMPOSE_FILES = (
    ROOT / "docker-compose.yml",
    ROOT / "docker-compose.gpu-nvidia.yml",
    ROOT / "docker-compose.gpu-amd.yml",
)


def _odysseus_environment_text(path: Path) -> str:
    text = path.read_text(encoding="utf-8")
    assert "odysseus:" in text
    start = text.index("odysseus:")
    chunk = text[start : start + 4000]
    env_start = chunk.index("environment:")
    env_chunk = chunk[env_start : env_start + 2500]
    return env_chunk


@pytest.mark.parametrize("compose_path", COMPOSE_FILES, ids=lambda p: p.name)
def test_compose_passes_lm_studio_url(compose_path):
    env = _odysseus_environment_text(compose_path)
    assert "LM_STUDIO_URL=${LM_STUDIO_URL:-}" in env


@pytest.mark.parametrize("compose_path", COMPOSE_FILES, ids=lambda p: p.name)
def test_compose_passes_private_caldav_flag(compose_path):
    env = _odysseus_environment_text(compose_path)
    assert "ODYSSEUS_ALLOW_PRIVATE_CALDAV=${ODYSSEUS_ALLOW_PRIVATE_CALDAV:-0}" in env
