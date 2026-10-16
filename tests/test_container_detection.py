"""Container detection must recognise Podman, not only Docker (#6673).

Podman creates no /.dockerenv and, on a cgroup v2 host, /proc/1/cgroup is
just ``0::/``. It marks its containers with /run/.containerenv and
``container=podman`` instead. Missing those made the app think it ran natively
and rewrite saved ``host.docker.internal`` endpoints to 127.0.0.1, which inside
the container is the container itself.
"""
import pytest

from src import endpoint_resolver as er
from src.host_docker_access import running_in_container


@pytest.fixture
def host(tmp_path):
    cgroup = tmp_path / "cgroup"
    cgroup.write_text("0::/\n", encoding="utf-8")  # cgroup v2: no runtime marker

    def detect(*, dockerenv=False, containerenv=False, environ=None):
        paths = {}
        for name, present in (("dockerenv", dockerenv), ("containerenv", containerenv)):
            path = tmp_path / name
            if present:
                path.touch()
            paths[f"{name}_path"] = str(path)
        return running_in_container(cgroup_path=str(cgroup), environ=environ or {}, **paths)

    return detect


def test_docker_is_a_container(host):
    assert host(dockerenv=True) is True


def test_podman_containerenv_is_a_container(host):
    assert host(containerenv=True) is True


def test_podman_environment_marker_is_a_container(host):
    assert host(environ={"container": "podman"}) is True


def test_native_host_is_not_a_container(host):
    assert host() is False


def test_lxc_marker_alone_is_not_a_container(host):
    # Native installs inside LXC have container=lxc but no host gateway alias,
    # so host.docker.internal still has to become loopback there.
    assert host(environ={"container": "lxc"}) is False


def test_podman_keeps_the_host_gateway_endpoint(monkeypatch, tmp_path):
    containerenv = tmp_path / ".containerenv"
    containerenv.touch()
    monkeypatch.setattr("src.host_docker_access.DOCKERENV_PATH", str(tmp_path / "missing"))
    monkeypatch.setattr("src.host_docker_access.CONTAINERENV_PATH", str(containerenv))
    monkeypatch.setattr("src.host_docker_access.CGROUP_PATH", str(tmp_path / "missing"))
    monkeypatch.delenv("container", raising=False)
    value = "http://host.docker.internal:1234/v1"
    assert er._rewrite_docker_host_for_native_runtime(value) == value
