import pytest
from fastapi import HTTPException

from core.platform_compat import _ssh_exec_argv
from routes.hwfit_routes import setup_hwfit_routes


def _endpoint(path: str):
    router = setup_hwfit_routes()
    for route in router.routes:
        if getattr(route, "path", "") == path:
            return route.endpoint
    raise AssertionError(f"{path} route not found")


@pytest.mark.parametrize(
    "path,kwargs",
    [
        ("/api/hwfit/system", {}),
        ("/api/hwfit/models", {"limit": 1}),
        ("/api/hwfit/profiles", {"model": "demo"}),
        ("/api/hwfit/image-models", {}),
    ],
)
def test_hwfit_routes_reject_ssh_option_host(path, kwargs):
    endpoint = _endpoint(path)

    with pytest.raises(HTTPException) as exc:
        endpoint(host="-oProxyCommand=sh", ssh_port="22", **kwargs)

    assert exc.value.status_code == 400


def test_hwfit_routes_reject_port_without_host():
    endpoint = _endpoint("/api/hwfit/system")

    with pytest.raises(HTTPException) as exc:
        endpoint(host="", ssh_port="2222")

    assert exc.value.status_code == 400


def test_ssh_argv_rejects_option_shaped_remote():
    with pytest.raises(ValueError):
        _ssh_exec_argv("-oProxyCommand=sh", "22", remote_cmd="true")
    with pytest.raises(ValueError):
        _ssh_exec_argv("alice@-oProxyCommand=sh", "22", remote_cmd="true")
