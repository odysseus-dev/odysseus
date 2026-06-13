from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "launch-windows.ps1"


def test_launch_windows_supports_optional_and_sidecars_switches():
    text = SCRIPT.read_text(encoding="utf-8")
    assert "InstallOptional" in text
    assert "WithSidecars" in text
    assert "requirements-optional.txt" in text
    assert "start-sidecars.ps1" in text
