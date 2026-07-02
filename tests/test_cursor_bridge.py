from integrations.cursor.bridge import (
    BridgeSettings,
    build_full_prompt,
    build_incremental_prompt,
    parse_model_map,
    resolve_requested_model,
)


def test_parse_model_map_supports_aliases():
    mapping = parse_model_map("cursor-fast=composer-2.5,gpt=gpt-5", "composer-2.5")
    assert mapping == {
        "cursor-fast": "composer-2.5",
        "gpt": "gpt-5",
    }


def test_parse_model_map_falls_back_to_default():
    assert parse_model_map("", "composer-2.5") == {"composer-2.5": "composer-2.5"}


def test_build_full_prompt_renders_roles_and_text_blocks():
    prompt = build_full_prompt(
        [
            {"role": "system", "content": "Be concise."},
            {"role": "user", "content": [{"type": "text", "text": "Hello"}]},
            {"role": "assistant", "content": "Hi"},
        ]
    )
    assert "SYSTEM:\nBe concise." in prompt
    assert "USER:\nHello" in prompt
    assert "ASSISTANT:\nHi" in prompt
    assert "latest user request" in prompt


def test_build_incremental_prompt_uses_latest_user_turn():
    prompt = build_incremental_prompt(
        [
            {"role": "system", "content": "Answer plainly."},
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "second"},
        ]
    )
    assert "SYSTEM:\nAnswer plainly." in prompt
    assert "USER:\nsecond" in prompt
    assert "USER:\nfirst" not in prompt


def test_resolve_requested_model_uses_direct_mapping(monkeypatch):
    monkeypatch.setenv("CURSOR_BRIDGE_MODEL_MAP", "composer=composer-2.5")
    monkeypatch.setenv("CURSOR_API_KEY", "")
    settings = BridgeSettings()
    exposed, actual = resolve_requested_model("composer", settings)
    assert (exposed, actual) == ("composer", "composer-2.5")
