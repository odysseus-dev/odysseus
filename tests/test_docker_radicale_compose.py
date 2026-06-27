"""Static contract tests for docker/radicale stack."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
RADICALE_DIR = ROOT / "docker" / "radicale"


def test_radicale_compose_exists_and_binds_tailscale_placeholder():
    compose = RADICALE_DIR / "docker-compose.yml"
    assert compose.is_file(), "docker/radicale/docker-compose.yml must exist"
    text = compose.read_text(encoding="utf-8")
    assert "RADICALE_BIND" in text
    assert "5232" in text
    assert "tomsquest/docker-radicale" in text


def test_radicale_env_example_documents_tailscale_bind():
    env_example = RADICALE_DIR / ".env.example"
    assert env_example.is_file()
    text = env_example.read_text(encoding="utf-8")
    assert "RADICALE_BIND" in text
    assert "100." in text or "Tailscale" in text


def test_radicale_readme_mentions_odysseus_caldav_settings():
    readme = RADICALE_DIR / "README.md"
    assert readme.is_file()
    text = readme.read_text(encoding="utf-8")
    assert "ODYSSEUS_ALLOW_PRIVATE_CALDAV" in text
    assert "Settings" in text or "Calendar" in text
