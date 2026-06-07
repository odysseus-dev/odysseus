from pathlib import Path


SLASH_COMMANDS = Path(__file__).resolve().parents[1] / "static" / "js" / "slashCommands.js"


def test_fireworks_setup_presets_use_openai_compatible_endpoint():
    src = SLASH_COMMANDS.read_text(encoding="utf-8")
    assert "fireworks: { name: 'Fireworks', url: 'https://api.fireworks.ai/inference/v1' }" in src
    assert "firepass:" in src
    assert "https://api.fireworks.ai/inference/v1" in src
    assert "accounts/fireworks/routers/kimi-k2p6-turbo" in src


def test_firepass_setup_pins_router_model():
    src = SLASH_COMMANDS.read_text(encoding="utf-8")
    assert "fd.append('pinned_models', JSON.stringify(detected.pinned_models));" in src
    assert "data.models.includes(detected.default_model)" in src
    assert "firepass:   { help: 'Fireworks Fire Pass'" in src
