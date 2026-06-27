"""Static contract for scripts/bootstrap-multi-machine.ps1."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "bootstrap-multi-machine.ps1"


def test_bootstrap_script_exists_and_seeds_env():
    assert SCRIPT.is_file(), "scripts/bootstrap-multi-machine.ps1 must exist"
    text = SCRIPT.read_text(encoding="utf-8")
    assert ".env.example" in text
    assert "ODYSSEUS_ALLOW_PRIVATE_CALDAV" in text
    assert "LLM_HOSTS" in text
    assert "multi_machine_env" in text or "verify_multi_machine" in text
