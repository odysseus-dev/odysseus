from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class TestOdysseusPowerShell:
    """odysseus.ps1 — unified launcher: structure and safeguards."""

    def test_standalone_in_launch_validateset(self):
        script = (ROOT / "odysseus.ps1").read_text(encoding="utf-8")
        assert '"standalone"' in script

    def test_force_build_switch_parameter(self):
        script = (ROOT / "odysseus.ps1").read_text(encoding="utf-8")
        assert "[switch]$ForceBuild" in script

    def test_build_standalone_launcher_function_exists(self):
        script = (ROOT / "odysseus.ps1").read_text(encoding="utf-8")
        assert "function Build-StandaloneLauncher" in script

    def test_standalone_run_guards_with_test_path(self):
        # Must check the exe exists (or ForceBuild) before running
        script = (ROOT / "odysseus.ps1").read_text(encoding="utf-8")
        assert "Test-Path" in script
        assert "Odysseus.exe" in script

    def test_build_uses_noconfirm_flag(self):
        # PyInstaller must be non-interactive
        script = (ROOT / "odysseus.ps1").read_text(encoding="utf-8")
        assert "--noconfirm" in script

    def test_build_uses_clean_flag(self):
        # Always produce a fresh build, not incremental
        script = (ROOT / "odysseus.ps1").read_text(encoding="utf-8")
        assert "--clean" in script

    def test_build_uses_onedir(self):
        # onedir keeps assets beside the exe; single-file would break asset paths
        script = (ROOT / "odysseus.ps1").read_text(encoding="utf-8")
        assert "--onedir" in script

    def test_update_uses_ff_only_git_pull(self):
        # Safe update — no force-push or rebase-style merges
        script = (ROOT / "odysseus.ps1").read_text(encoding="utf-8")
        assert "--ff-only" in script

    def test_app_bind_and_port_passed_to_standalone(self):
        # Launcher needs host/port from env vars
        script = (ROOT / "odysseus.ps1").read_text(encoding="utf-8")
        assert "APP_BIND" in script
        assert "APP_PORT" in script

    def test_force_build_flag_parsed_from_gnu_args(self):
        script = (ROOT / "odysseus.ps1").read_text(encoding="utf-8")
        assert "force-build" in script


class TestDeprecatedBuildScript:
    """build-windows-portable.ps1 — deprecated, forwards to odysseus standalone."""

    def test_deprecated_message_present(self):
        script = (ROOT / "build-windows-portable.ps1").read_text(encoding="utf-8")
        assert "DEPRECATED" in script.upper()

    def test_forwards_to_standalone_launch(self):
        script = (ROOT / "build-windows-portable.ps1").read_text(encoding="utf-8")
        assert "standalone" in script.lower()

    def test_forwards_with_force_build(self):
        script = (ROOT / "build-windows-portable.ps1").read_text(encoding="utf-8")
        assert "ForceBuild" in script or "force-build" in script.lower()


class TestDeprecatedUpdateBat:
    """update_windows.bat — deprecated, forwards to odysseus update --launch docker."""

    def test_deprecated_message_present(self):
        script = (ROOT / "update_windows.bat").read_text(encoding="utf-8")
        assert "DEPRECATED" in script.upper()

    def test_forwards_to_odysseus_docker_update(self):
        script = (ROOT / "update_windows.bat").read_text(encoding="utf-8")
        lowered = script.lower()
        assert "odysseus" in lowered
        assert "docker" in lowered
