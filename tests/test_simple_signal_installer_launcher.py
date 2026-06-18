from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
INSTALLER = (ROOT / "installer.py").read_text(encoding="utf-8")


def test_extension_opening_page_has_direct_navigation_fallback():
    assert 'var health = target + "api/health";' in INSTALLER
    assert "function navigate()" in INSTALLER
    assert "setTimeout(navigate, 8000);" in INSTALLER
    assert "window.location.replace(target);" in INSTALLER
    assert 'id="open-now"' in INSTALLER


def test_extension_router_does_not_spawn_duplicate_server_when_port_is_live():
    assert "import socket" in INSTALLER
    assert "import time" in INSTALLER
    assert "def _is_port_open(value):" in INSTALLER
    assert "def _launch_lock_recent(max_age=45):" in INSTALLER
    assert 'lock_path = os.path.join(install_dir, ".odysseus-launch.lock")' in INSTALLER
    assert 'socket.create_connection(("127.0.0.1", int(value)), timeout=0.35)' in INSTALLER
    assert "if os.path.exists(ps1_path) and not _is_port_open(port) and not _launch_lock_recent():" in INSTALLER
