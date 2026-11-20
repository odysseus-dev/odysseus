from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def test_image_auto_detect_excludes_retired_models():
    for rel_path in ("mcp_servers/image_gen_server.py", "src/ai_interaction.py"):
        text = (REPO_ROOT / rel_path).read_text(encoding="utf-8")
        assert '"gpt-image-2.6-sunburst"' in text
        assert '"gpt-image-2.6-flare"' in text
        assert '"dall-e-3"' not in text
        assert '"gpt-image-1"' not in text


def test_vision_auto_detect_excludes_retired_models():
    text = (REPO_ROOT / "src/document_processor.py").read_text(encoding="utf-8")
    assert '"claude-sonnet-5-5"' in text
    assert '"claude-opus-4-8"' in text
    assert '"gemini-3.6-flash"' in text
    for retired in ("claude-sonnet-4-5-20250929", "claude-opus-4-20250514", "gemini-2.0-flash"):
        assert retired not in text
