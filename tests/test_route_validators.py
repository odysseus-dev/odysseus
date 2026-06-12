import pytest
from fastapi import HTTPException

from routes._validators import validate_remote_host, validate_ssh_port


def test_validate_ssh_port_rejects_shell_payload():
    for port in ["22;id", "$(id)", "-p 22", "0", "65536"]:
        with pytest.raises(HTTPException):
            validate_ssh_port(port)
    assert validate_ssh_port("2222") == "2222"


def test_validate_remote_host_rejects_ssh_option_shape():
    for host in [
        "-oProxyCommand=sh",
        "alice@-oProxyCommand=sh",
        "--",
        "-p2222",
    ]:
        with pytest.raises(HTTPException):
            validate_remote_host(host)
    assert validate_remote_host("alice@gpu-box_1") == "alice@gpu-box_1"
