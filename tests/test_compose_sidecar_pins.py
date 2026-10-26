"""Sidecar images must be pinned to a versioned tag and an immutable digest.

A bare or ``:latest`` reference changes underneath an existing install on the
next ``docker compose pull``/``up``, so two hosts on the same commit can run
different ChromaDB or ntfy builds. The ``odysseus`` service is excluded: it is
the project's own image, selected through ``ODYSSEUS_IMAGE``. The standalone
GPU files are held equal to these services by test_gpu_compose_standalone.py.
"""

import re
from pathlib import Path

import pytest
import yaml

COMPOSE = Path(__file__).resolve().parents[1] / "docker-compose.yml"
PINNED = re.compile(r"^[\w.\-/]+:(?P<tag>[\w.\-]+)@sha256:[0-9a-f]{64}$")


def _sidecars():
    services = yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))["services"]
    return {name: svc["image"] for name, svc in services.items() if name != "odysseus"}


def test_expected_sidecars_present():
    assert {"chromadb", "searxng", "ntfy"} <= set(_sidecars())


@pytest.mark.parametrize("name", sorted(_sidecars()))
def test_sidecar_image_has_version_tag_and_digest(name):
    image = _sidecars()[name]
    match = PINNED.match(image)
    assert match, f"{name}: expected <repo>:<tag>@sha256:<digest>, got {image!r}"
    assert match.group("tag") != "latest", f"{name}: pin a release tag, not latest"
