"""Policy checks for explicit host Docker access from a container."""

import os
import stat
from collections.abc import Mapping


HOST_DOCKER_ENV_VAR = "ODYSSEUS_ENABLE_HOST_DOCKER"
HOST_DOCKER_SOCKET_PATH = "/var/run/docker.sock"
DOCKERENV_PATH = "/.dockerenv"
# Podman (and other OCI runtimes) create no /.dockerenv; they mark containers
# with this file and container=podman instead.
CONTAINERENV_PATH = "/run/.containerenv"
CGROUP_PATH = "/proc/1/cgroup"

HOST_DOCKER_ACCESS_HINT = (
    "Local Docker daemon access is disabled inside the Odysseus container; a "
    "Docker CLI alone is not enough. Default Docker Compose intentionally does "
    "not mount the host Docker socket. Raw socket access is high-trust and can "
    "grant broad control over the host Docker daemon. If you accept that risk, "
    "enable docker/host-docker.yml. Remote server Docker workflows over SSH "
    "remain preferred."
)


def running_in_container(
    dockerenv_path: str | None = None,
    cgroup_path: str | None = None,
    *,
    containerenv_path: str | None = None,
    environ: Mapping[str, str] | None = None,
) -> bool:
    env = os.environ if environ is None else environ
    if (os.path.exists(dockerenv_path or DOCKERENV_PATH)
            or os.path.exists(containerenv_path or CONTAINERENV_PATH)
            or env.get("container", "").strip().lower() == "podman"):
        return True
    # On cgroup v2 hosts /proc/1/cgroup is just "0::/", so this only helps on v1.
    try:
        with open(cgroup_path or CGROUP_PATH, "r", encoding="utf-8") as handle:
            contents = handle.read()
    except OSError:
        return False
    return any(token in contents for token in ("docker", "containerd", "kubepods"))


def host_docker_access_enabled(
    socket_path: str = HOST_DOCKER_SOCKET_PATH,
    *,
    environ: Mapping[str, str] | None = None,
) -> bool:
    env = os.environ if environ is None else environ
    if env.get(HOST_DOCKER_ENV_VAR, "").strip().lower() != "true":
        return False
    try:
        mode = os.stat(socket_path).st_mode
    except OSError:
        return False
    return stat.S_ISSOCK(mode)


def local_docker_available(
    *,
    cli_available: bool,
    in_container: bool | None = None,
    environ: Mapping[str, str] | None = None,
    socket_path: str = HOST_DOCKER_SOCKET_PATH,
) -> bool:
    if not cli_available:
        return False
    containerized = running_in_container() if in_container is None else in_container
    if not containerized:
        return True
    return host_docker_access_enabled(socket_path, environ=environ)
