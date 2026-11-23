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


@pytest.fixture
def runtime(monkeypatch):
    """Fake the real marker paths (no injected parameters), cgroup v2 host."""
    import builtins
    import io
    import os

    real_exists, real_open = os.path.exists, builtins.open
    present = set()

    def fake_exists(path):
        if path in ("/.dockerenv", "/run/.containerenv"):
            return path in present
        return real_exists(path)

    def fake_open(path, *args, **kwargs):
        if path == "/proc/1/cgroup":
            return io.StringIO("0::/\n")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(os.path, "exists", fake_exists)
    monkeypatch.setattr(builtins, "open", fake_open)
    monkeypatch.delenv("container", raising=False)
    return present


ENDPOINT = "http://host.docker.internal:1234/v1"


def test_podman_containerenv_keeps_the_host_gateway_endpoint(runtime):
    runtime.add("/run/.containerenv")
    assert er._rewrite_docker_host_for_native_runtime(ENDPOINT) == ENDPOINT


def test_podman_environment_keeps_the_host_gateway_endpoint(runtime, monkeypatch):
    monkeypatch.setenv("container", "podman")
    assert er._rewrite_docker_host_for_native_runtime(ENDPOINT) == ENDPOINT


def test_native_lxc_still_rewrites_to_loopback(runtime, monkeypatch):
    monkeypatch.setenv("container", "lxc")
    assert (
        er._rewrite_docker_host_for_native_runtime(ENDPOINT)
        == "http://127.0.0.1:1234/v1"
    )
