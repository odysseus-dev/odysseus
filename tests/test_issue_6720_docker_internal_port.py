import json
import os
import pathlib
import re

from src.constants import internal_api_base


REPO_ROOT = pathlib.Path(__file__).resolve().parent.parent


def test_dockerfile_app_port_matches_uvicorn_listen_port():
    dockerfile = (REPO_ROOT / "Dockerfile").read_text(encoding="utf-8")

    env_match = re.search(r"^ENV\s+APP_PORT=(\d+)\s*$", dockerfile, re.MULTILINE)
    assert env_match is not None, "Dockerfile must set ENV APP_PORT=<port>"
    env_port = env_match.group(1)

    cmd_match = re.search(r"^CMD\s+(\[.+\])\s*$", dockerfile, re.MULTILINE)
    assert cmd_match is not None, "Dockerfile must define JSON-array CMD"
    cmd_args = json.loads(cmd_match.group(1))
    assert "--port" in cmd_args, "Dockerfile CMD must specify --port"
    uvicorn_port = cmd_args[cmd_args.index("--port") + 1]

    assert env_port == uvicorn_port == "7000"


def test_internal_api_base_uses_container_app_port_when_override_unset(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_INTERNAL_BASE", raising=False)
    monkeypatch.setenv("APP_PORT", "7000")
    assert internal_api_base() == "http://127.0.0.1:7000"


def test_internal_api_base_keeps_non_docker_7011_fallback(monkeypatch):
    monkeypatch.delenv("ODYSSEUS_INTERNAL_BASE", raising=False)
    monkeypatch.delenv("APP_PORT", raising=False)
    assert internal_api_base() == "http://127.0.0.1:7011"
