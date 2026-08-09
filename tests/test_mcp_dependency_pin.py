"""Regression coverage for the built-in MCP server API contract."""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_requirements_pin_the_mcp_v1_server_decorator_api():
    requirements = (REPO_ROOT / "requirements.txt").read_text(encoding="utf-8").splitlines()

    assert "mcp==1.28.1" in requirements
