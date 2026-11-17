from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1] / "src/agent_loop.py"
).read_text(encoding="utf-8")


def test_structured_documents_bypass_blind_append_shortcut():
    assert "_active_doc_is_structured = bool(" in SCRIPT
    assert "and not _active_doc_is_structured" in SCRIPT
    assert "Logs, tables, checklists" in SCRIPT
