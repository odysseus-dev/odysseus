from src.reminder_personas import synthesis_system_prompt


def test_default_ai_synthesis_is_brief_and_plain():
    prompt = synthesis_system_prompt("").lower()

    assert "under 10 words" in prompt
    assert "no greeting" in prompt
    assert "no preamble" in prompt


def test_persona_ai_synthesis_keeps_the_same_brief_limit():
    prompt = synthesis_system_prompt("spark").lower()

    assert "under 10 words" in prompt
    assert "one plain reminder sentence" in prompt
