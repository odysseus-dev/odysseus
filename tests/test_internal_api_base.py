"""internal_api_base() resolution + a guard that loopback call sites use it."""
import importlib
import pathlib

import pytest

import core.constants as cc


def _base(monkeypatch, **env):
    for k in ("ODYSSEUS_INTERNAL_BASE", "APP_PORT"):
        monkeypatch.delenv(k, raising=False)
    for k, v in env.items():
        monkeypatch.setenv(k, v)
    return cc.internal_api_base()


def test_default_is_legacy_7000(monkeypatch):
    assert _base(monkeypatch) == "http://127.0.0.1:7000"


def test_app_port_is_honored(monkeypatch):
    assert _base(monkeypatch, APP_PORT="7860") == "http://127.0.0.1:7860"


def test_explicit_override_wins_and_is_stripped(monkeypatch):
    # Override beats APP_PORT and trailing slash is trimmed.
    assert _base(monkeypatch, APP_PORT="7860",
                 ODYSSEUS_INTERNAL_BASE="https://proxy.example/") == "https://proxy.example"


def test_uses_127_not_localhost(monkeypatch):
    # 127.0.0.1 avoids IPv6/DNS ambiguity for the strictly-local loopback.
    assert "localhost" not in _base(monkeypatch)


def test_no_hardcoded_loopback_left_in_call_sites():
    # Regression guard: the converted files must not reintroduce the literal.
    root = pathlib.Path(__file__).resolve().parent.parent
    for rel in (
        "src/tool_implementations.py",
        "src/cookbook_serve_lifecycle.py",
        "src/builtin_actions.py",
        "routes/task_routes.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        # Allow it only inside comments; flag any code occurrence.
        for ln in text.splitlines():
            stripped = ln.strip()
            if stripped.startswith("#"):
                continue
            assert "localhost:7000" not in ln, f"{rel}: hardcoded loopback URL: {ln.strip()}"
