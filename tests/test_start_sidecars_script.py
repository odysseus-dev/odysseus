"""Static contract for scripts/start-sidecars.ps1."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "start-sidecars.ps1"


def test_start_sidecars_script_exists_and_targets_all_sidecars():
    assert SCRIPT.is_file(), "scripts/start-sidecars.ps1 must exist"
    text = SCRIPT.read_text(encoding="utf-8")
    for service in ("chromadb", "searxng", "ntfy"):
        assert service in text, f"missing docker compose service {service}"
    assert "docker compose up" in text.lower() or "docker-compose up" in text.lower()
    assert "8080" in text, "must wait on SearXNG loopback port 8080"
