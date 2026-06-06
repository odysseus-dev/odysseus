#!/usr/bin/env python3
"""Regenerate docker-compose.gpu-*.yml from base + docker/gpu.*.yml overlays.

The standalone files exist for stack UIs that accept only one Compose file.
Run from repo root after editing docker-compose.yml or docker/gpu.*.yml:

    python scripts/sync-gpu-compose-standalone.py
"""

from __future__ import annotations

import copy
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]

STANDALONE_HEADERS = {
    "amd": """\
# Standalone AMD ROCm GPU Compose file for stack-management UIs (Portainer,
# Coolify, Dockhand, etc.) that accept only a single Compose file and do not
# reliably honor COMPOSE_FILE or multiple `-f` overlays.
#
# This is equivalent to: docker-compose.yml + docker/gpu.amd.yml.
# The base docker-compose.yml plus the docker/gpu.amd.yml overlay remain the
# source of truth — CLI users should keep using the COMPOSE_FILE overlay
# workflow. Keep this file in sync by running:
#   python scripts/sync-gpu-compose-standalone.py
#
# Requires ROCm drivers on the host (kfd + DRI devices) and the host user
# running Docker in the `video` and `render` groups. Set RENDER_GID to your
# host's numeric render group id when needed. See docker/gpu.amd.yml for details.
""",
    "nvidia": """\
# Standalone NVIDIA GPU Compose file for stack-management UIs (Portainer,
# Coolify, Dockhand, etc.) that accept only a single Compose file and do not
# reliably honor COMPOSE_FILE or multiple `-f` overlays.
#
# This is equivalent to: docker-compose.yml + docker/gpu.nvidia.yml.
# The base docker-compose.yml plus the docker/gpu.nvidia.yml overlay remain the
# source of truth — CLI users should keep using the COMPOSE_FILE overlay
# workflow. Keep this file in sync by running:
#   python scripts/sync-gpu-compose-standalone.py
#
# Requires the NVIDIA Container Toolkit on the host. See docker/gpu.nvidia.yml
# for setup details.
""",
}


def _deep_merge(base: dict, overlay: dict) -> dict:
    result = copy.deepcopy(base)
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        elif isinstance(value, list) and isinstance(result.get(key), list):
            result[key] = copy.deepcopy(result[key]) + copy.deepcopy(value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def merge_compose(base: dict, overlay: dict) -> dict:
    merged = copy.deepcopy(base)
    for name, overlay_svc in overlay.get("services", {}).items():
        if name in merged["services"]:
            merged["services"][name] = _deep_merge(merged["services"][name], overlay_svc)
        else:
            merged["services"][name] = copy.deepcopy(overlay_svc)
    if overlay.get("volumes"):
        merged["volumes"] = _deep_merge(merged.get("volumes", {}), overlay["volumes"])
    return merged


def main() -> None:
    base = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    pairs = (
        ("amd", ROOT / "docker" / "gpu.amd.yml", ROOT / "docker-compose.gpu-amd.yml"),
        ("nvidia", ROOT / "docker" / "gpu.nvidia.yml", ROOT / "docker-compose.gpu-nvidia.yml"),
    )
    for key, overlay_path, out_path in pairs:
        overlay = yaml.safe_load(overlay_path.read_text(encoding="utf-8"))
        merged = merge_compose(base, overlay)
        body = yaml.safe_dump(merged, sort_keys=False, default_flow_style=False)
        out_path.write_text(STANDALONE_HEADERS[key] + body, encoding="utf-8")
        print(f"wrote {out_path.relative_to(ROOT)}")


if __name__ == "__main__":
    main()
