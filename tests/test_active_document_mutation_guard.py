from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1] / "src/agent_loop.py"
).read_text(encoding="utf-8")


def test_active_document_mutations_require_editor_tool_evidence():
    assert "def _active_document_mutation_requires_tool(" in SCRIPT
    assert "def _has_successful_active_document_mutation(" in SCRIPT
    assert "_active_document_mutation_turn" in SCRIPT
    assert "active document mutation answered without editor tool evidence" in SCRIPT


def test_guard_allows_editor_tools_or_one_clarification():
    assert '{"edit_document", "update_document", "suggest_document"}' in SCRIPT
    assert "call `ask_user` once instead" in SCRIPT


def test_active_email_reply_drafts_are_editor_mutations():
    assert "_is_email_document_obj(active_document) and _email_reply_draft_requested(text)" in SCRIPT


def test_guard_uses_request_tools_before_retrieval_tools_are_initialized():
    call = """_active_document_mutation_requires_tool(
        _last_user,
        active_document,
        relevant_tools,
    )"""
    assert call in SCRIPT
