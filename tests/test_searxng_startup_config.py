from pathlib import Path

import yaml


ROOT = Path(__file__).resolve().parents[1]


def test_searxng_removes_non_web_startup_engines():
    config = yaml.safe_load((ROOT / "config" / "searxng" / "settings.yml").read_text())

    removed = set(config["use_default_settings"]["engines"]["remove"])

    assert {"ahmia", "torch", "radio browser"} <= removed


def test_searxng_limiter_config_is_mounted():
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text())
    volumes = compose["services"]["searxng"]["volumes"]

    assert "./config/searxng/limiter.toml:/etc/searxng/limiter.toml:ro,z" in volumes
    assert (ROOT / "config" / "searxng" / "limiter.toml").exists()
