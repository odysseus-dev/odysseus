"""One dependency catalog, platform-specific implementations.

Availability is decided by the backend registry from the selected target —
never from the browser — and the same registry backs the install endpoint, so
the UI cannot offer an install the server would refuse.
"""
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from routes.shell_routes import (  # noqa: E402
    DEPENDENCY_PLATFORMS,
    dependency_platform,
    effective_platform_key,
)


def _pkg(name, pip="x", kind="python"):
    return {"name": name, "pip": pip, "kind": kind}


def test_registry_covers_every_shipped_dependency():
    source = (ROOT / "routes" / "shell_routes.py").read_text(encoding="utf-8")
    # Every row in the catalog must have a registry entry, or the resolver must
    # explicitly fall through. This guards against a new row silently keeping a
    # Linux install button on Windows.
    import re

    names = set(re.findall(r'"name":\s*"([a-z0-9_]+)"', source))
    missing = sorted(n for n in names if n not in DEPENDENCY_PLATFORMS)
    # Pure-Python tools that genuinely work on every platform. Anything else
    # must have an explicit registry entry.
    allowed = {
        "hf_transfer", "rembg", "gfpgan", "realesrgan", "librosa", "markitdown",
        "python-docx", "TTS", "bark", "faster-whisper", "insightface",
        "onnxruntime", "onnxruntime-gpu", "hdbscan", "APFEL",
    }
    assert not (set(missing) - allowed), f"unregistered dependencies: {set(missing) - allowed}"


def test_llama_cpp_is_prebuilt_on_windows_and_pip_elsewhere():
    win = dependency_platform(_pkg("llama_cpp", "llama-cpp-python[server]"), "windows")
    assert win["support"] == "yes" and win["method"] == "prebuilt" and win["install_enabled"]

    lin = dependency_platform(_pkg("llama_cpp", "llama-cpp-python[server]"), "linux")
    assert lin["method"] == "pip"

    mac = dependency_platform(_pkg("llama_cpp", "llama-cpp-python[server]"), "macos")
    assert mac["support"] == "manual" and not mac["install_enabled"]


def test_apple_only_and_native_windows_only_engines_are_unsupported():
    for name in ("mlx_lm", "mflux"):
        for key in ("windows", "linux", "wsl"):
            assert dependency_platform(_pkg(name), key)["support"] == "no"
        assert dependency_platform(_pkg(name), "macos")["install_enabled"]

    for name in ("vllm", "sglang"):
        assert dependency_platform(_pkg(name), "windows")["support"] == "no"
        assert dependency_platform(_pkg(name), "linux")["install_enabled"]
        assert dependency_platform(_pkg(name), "macos")["support"] == "no"


def test_tmux_is_not_applicable_to_native_windows():
    win = dependency_platform(_pkg("tmux", "", kind="system"), "windows")
    assert win["support"] == "no" and not win["install_enabled"] and win["reason"]
    assert dependency_platform(_pkg("tmux", "", kind="system"), "linux")["method"] == "system"


def test_effective_platform_comes_from_the_backend_target(monkeypatch):
    assert effective_platform_key(host="", platform="", os_id="") in {"windows", "linux", "macos", "wsl"}
    assert effective_platform_key(host="10.0.0.5", platform="windows", os_id="") == "windows"
    assert effective_platform_key(host="10.0.0.5", platform="linux", os_id="") == "linux"
    assert effective_platform_key(host="10.0.0.5", platform="darwin", os_id="") == "macos"
    assert effective_platform_key(host="10.0.0.5", platform="termux", os_id="") == "linux"


@pytest.mark.asyncio
async def test_packages_endpoint_reports_platform_per_row(monkeypatch):
    from types import SimpleNamespace
    import routes.shell_routes as shell_routes
    from starlette.requests import Request

    monkeypatch.setattr(shell_routes, "_require_admin", lambda request: None)
    monkeypatch.setattr(shell_routes, "_reject_cross_site", lambda request: None)
    monkeypatch.setattr(shell_routes, "_PACKAGE_STATUS_CACHE", {})
    monkeypatch.setattr(shell_routes.shutil, "which", lambda name: None)
    monkeypatch.setattr(shell_routes, "IS_APPLE_SILICON", False)

    router = shell_routes.setup_shell_routes()
    endpoint = next(
        r.endpoint for r in router.routes
        if getattr(r, "path", None) == "/api/cookbook/packages" and "GET" in r.methods
    )
    http_req = Request({
        "type": "http", "method": "GET", "path": "/api/cookbook/packages", "headers": [],
        "app": SimpleNamespace(state=SimpleNamespace(auth_manager=SimpleNamespace(is_configured=True))),
    })
    http_req.state.current_user = "admin"
    data = await endpoint(http_req, None, None, None, None, None, None)

    rows = {p["name"]: p for p in data["packages"]}
    # The key is computed from the local backend, so on this Windows host the
    # rows must carry a native-Windows verdict.
    for name in ("llama_cpp", "tmux", "mlx_lm", "vllm", "sglang"):
        assert "platform" in rows[name], f"{name} has no platform block"
        assert rows[name]["platform"]["platform"] == "windows"
    assert rows["llama_cpp"]["platform"]["method"] == "prebuilt"
    assert rows["mlx_lm"]["platform"]["support"] == "no"
    assert rows["tmux"]["platform"]["support"] == "no"


def test_frontend_reads_the_backend_platform_block():
    source = (ROOT / "static" / "js" / "cookbook.js").read_text(encoding="utf-8")

    assert "const _winUnsupported" not in source, "browser-side OS gating must be gone"
    assert "const unsupported = (pkg.platform && pkg.platform.support === 'no');" in source
    assert 'data-dep-prebuilt="${esc(pkg.name)}"' in source
    assert "if (btn.dataset.depPrebuilt)" in source
