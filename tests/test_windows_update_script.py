from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_windows_update_script_uses_safe_docker_update_flow():
    script = (ROOT / "update_windows.bat").read_text(encoding="utf-8")
    lowered = script.lower()

    assert 'pushd "%~dp0"' in lowered
    assert "where git" in lowered
    assert "where docker" in lowered
    assert "docker compose version" in lowered
    assert "git pull --ff-only" in lowered
    assert "docker compose up -d --build" in lowered
    assert "docker image prune -f" in lowered
    assert "pause" in lowered
