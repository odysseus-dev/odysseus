"""Regression guard for issue #1414 — a broken upstream `searxng:latest` tag
(2026.6.2 crashed on boot with KeyError: 'default_doi_resolver') failed the
searxng healthcheck, and because `odysseus` waits on it via
`depends_on: condition: service_healthy`, the whole app never started on fresh
Docker installs.

Pin the SearXNG image to a known-good tag so a bad upstream `latest` can't block
startup. This guards that the pin stays in place.
"""
import re
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ROOT / "docker-compose.yml"


def test_searxng_image_is_pinned_not_latest():
    text = COMPOSE.read_text(encoding="utf-8")
    image = yaml.safe_load(text)["services"]["searxng"]["image"]
    assert re.fullmatch(
        r"docker\.io/searxng/searxng:\d{4}\.\d[^\s@]*@sha256:[0-9a-f]{64}", image
    ), f"SearXNG must use a versioned immutable upstream image: {image!r}"


def test_all_standalone_compose_files_select_the_same_searxng_image():
    expected = yaml.safe_load(COMPOSE.read_text())["services"]["searxng"]["image"]
    for name in ("docker-compose.gpu-amd.yml", "docker-compose.gpu-nvidia.yml"):
        actual = yaml.safe_load((ROOT / name).read_text())["services"]["searxng"]["image"]
        assert actual == expected, f"{name} selects a different SearXNG image"
