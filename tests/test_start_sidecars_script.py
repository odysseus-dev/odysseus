from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start-sidecars.ps1"


def test_start_sidecars_script_exists_and_targets_core_services():
    assert SCRIPT.is_file()
    text = SCRIPT.read_text(encoding="utf-8")
    assert "chromadb" in text
    assert "searxng" in text
    assert "docker compose" in text
