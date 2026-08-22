"""Static contracts for the rootless Podman Compose overlays."""

from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
PODMAN = ROOT / "docker" / "podman.yml"
NVIDIA = ROOT / "docker" / "podman.gpu-nvidia.yml"
AMD = ROOT / "docker" / "podman.gpu-amd.yml"
DOCS = ROOT / "docs" / "podman.md"


def _load(path: Path) -> dict:
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def _service(path: Path) -> dict:
    compose = _load(path)
    assert set(compose["services"]) == {"odysseus"}
    return compose["services"]["odysseus"]


def _environment(entries: list[str]) -> dict[str, str]:
    return dict(entry.split("=", 1) for entry in entries)


def test_rootless_overlay_preserves_host_owned_bind_mounts():
    service = _service(PODMAN)

    assert service["userns_mode"] == "keep-id:uid=0,gid=0"
    assert _environment(service["environment"]) == {"PUID": "0", "PGID": "0"}
    assert service["security_opt"] == ["no-new-privileges:true"]


def test_rootless_overlay_does_not_expose_a_control_socket_or_host_network():
    text = PODMAN.read_text(encoding="utf-8")

    assert "/var/run/docker.sock" not in text
    assert "/run/user/" not in text
    assert "podman.sock" not in text
    assert "network_mode: host" not in text


def test_nvidia_overlay_uses_cdi_without_legacy_hook_selector():
    service = _service(NVIDIA)

    assert service["devices"] == ["nvidia.com/gpu=all"]
    assert service["security_opt"] == ["label=disable"]
    assert "environment" not in service


def test_amd_overlay_preserves_rootless_device_groups():
    service = _service(AMD)

    assert service["devices"] == ["/dev/kfd", "/dev/dri"]
    assert service["group_add"] == ["keep-groups"]


def test_docs_use_supported_wrapper_without_mutating_host_setup():
    text = DOCS.read_text(encoding="utf-8")

    assert "podman compose -f docker-compose.yml -f docker/podman.yml" in text
    assert "sudo podman" in text
    assert "podman-compose -f" not in text
    assert "enable --now podman.socket" not in text
    assert "ODYSSEUS_ADMIN_PASSWORD=" not in text
    assert "network_mode: host" not in text


def test_readme_links_the_podman_guide():
    readme = (ROOT / "README.md").read_text(encoding="utf-8")

    assert "[Podman guide](docs/podman.md)" in readme
