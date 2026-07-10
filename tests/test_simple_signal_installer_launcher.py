from pathlib import Path

from installer import copy_extension_files


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "installer.py").read_text(encoding="utf-8")
INSTALLER_SPEC = (ROOT / "installer.spec").read_text(encoding="utf-8")
ODYSSEUS_SPEC = (ROOT / "Odysseus_Setup.spec").read_text(encoding="utf-8")


def test_extension_uses_original_top_level_redirect_to_shared_backend():
    assert 'EXTENSION_PORT = int(os.environ.get("ODYSSEUS_EXTENSION_PORT", "7000"))' in INSTALLER
    assert 'target_url = f"http://127.0.0.1:{EXTENSION_PORT}/"' in INSTALLER
    assert "window.top.location.href=" in INSTALLER
    assert "window.location.replace(target);" not in INSTALLER
    assert "Opening Odysseus..." not in INSTALLER


def test_extension_does_not_default_to_a_second_odysseus_instance():
    assert 'os.environ.get("ODYSSEUS_EXTENSION_PORT", "7017")' not in INSTALLER


def test_extension_router_preserves_simple_signal_integration_contract():
    assert "if os.path.exists(ps1_path):" in INSTALLER
    assert '["powershell.exe", "-ExecutionPolicy", "Bypass", "-WindowStyle", "Hidden", "-File", ps1_path]' in INSTALLER
    assert 'cwd=install_dir\n    )' in INSTALLER


def test_extension_update_merges_assets_without_removing_existing_directories():
    copy_function = INSTALLER.split("def copy_extension_files", 1)[1].split("def python_version_for_command", 1)[0]
    assert "shutil.copytree(src, target, dirs_exist_ok=True)" in copy_function
    assert "shutil.rmtree(target" not in copy_function


def test_extension_update_preserves_existing_nested_files(tmp_path):
    source = tmp_path / "source"
    extension = tmp_path / "extension"
    (source / "static").mkdir(parents=True)
    (source / "static" / "current.txt").write_text("current", encoding="utf-8")
    (source / "package.json").write_text('{"version":"1.0.7"}', encoding="utf-8")
    (extension / "static").mkdir(parents=True)
    (extension / "static" / "preserved.txt").write_text("preserved", encoding="utf-8")

    copy_extension_files(source, extension)

    assert (extension / "static" / "current.txt").read_text(encoding="utf-8") == "current"
    assert (extension / "static" / "preserved.txt").read_text(encoding="utf-8") == "preserved"
    assert 'window.top.location.href="http://127.0.0.1:7000"' in (extension / "index.html").read_text(encoding="utf-8")
    assert '"version": "1.0.7"' in (extension / "manifest.json").read_text(encoding="utf-8")


def test_extension_manifest_uses_packaged_application_version():
    assert "def get_extension_version(source_dir: Path) -> str:" in INSTALLER
    assert '"version": get_extension_version(source_dir)' in INSTALLER
    assert "('package.json', '.')" in INSTALLER_SPEC
    assert "('package.json', '.')" in ODYSSEUS_SPEC
