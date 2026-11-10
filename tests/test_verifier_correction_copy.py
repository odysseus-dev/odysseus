from pathlib import Path


AGENT_LOOP = Path(__file__).resolve().parents[1] / "src" / "agent_loop.py"


def test_verifier_correction_uses_plain_continuation_copy():
    source = AGENT_LOOP.read_text(encoding="utf-8")

    assert '_note = "\\n\\nAnd also...\\n\\n"' in source
    assert "Double-checked the work and found something to fix" not in source


def test_verifier_followup_forbids_repeating_the_visible_answer():
    source = AGENT_LOOP.read_text(encoding="utf-8")

    assert "state only the corrected or newly" in source
    assert "append-only correction" in source
    assert "Do not add another introduction" in source
    assert "repeat accurate parts" in source
    assert "restate the entire answer" in source
