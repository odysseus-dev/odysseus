from types import SimpleNamespace

from src.action_intents import classify_tool_intent
from src.turn_contract import (
    requested_capabilities,
    required_read_operation_for_request,
    resolve_turn_contract,
    selected_tools_for_request,
)


def _schema(name: str) -> dict:
    return {"type": "function", "function": {"name": name, "parameters": {"type": "object", "properties": {}}}}


def _policy():
    return SimpleNamespace(
        all_disabled_names=lambda: set(),
        blocks=lambda _name: False,
        disable_mcp=False,
    )


def test_chat_action_intents_promote_documents_and_library_reads():
    docs = classify_tool_intent("What documents do I have?")
    assert docs.needs_tools is True
    assert docs.category == "documents"

    lib = classify_tool_intent("What is in my library?")
    assert lib.needs_tools is True
    assert lib.category == "library"

    research_lib = classify_tool_intent("What is in my research library?")
    assert research_lib.needs_tools is True
    assert research_lib.category == "research"

    kb = classify_tool_intent("What is in my personal knowledge base?")
    assert kb.needs_tools is True
    assert kb.category == "memory"


def test_explanatory_document_and_library_questions_stay_plain_chat():
    assert classify_tool_intent("How do I use the documents feature?").needs_tools is False
    assert classify_tool_intent("Can you explain how the research library works?").needs_tools is False


def test_turn_contract_distinguishes_documents_research_library_kb_and_generic_library():
    schemas = [
        _schema("manage_documents"),
        _schema("create_document"),
        _schema("manage_research"),
        _schema("manage_memory"),
        _schema("manage_skills"),
    ]
    policy = _policy()

    # 1. Documents inventory preserves manage_documents
    doc_caps = requested_capabilities("What documents do I have?")
    assert "documents" in doc_caps
    doc_contract = resolve_turn_contract(
        capabilities=doc_caps,
        schemas=schemas,
        policy=policy,
        selected_tools=selected_tools_for_request("What documents do I have?"),
        message="What documents do I have?",
    )
    assert "manage_documents" in doc_contract.offered

    # 2. Research Library routes to manage_research, not documents or personal RAG
    res_op = required_read_operation_for_request("What is in my research library?")
    assert res_op is not None
    assert res_op.tool == "manage_research"
    assert res_op.args == {"action": "list"}

    # 3. Personal knowledge base routes to memory (personal RAG), not documents
    kb_caps = requested_capabilities("What is in my personal knowledge base?")
    assert kb_caps == frozenset({"memory"})

    # 4. Generic library preserves multi-surface library tools instead of collapsing to memory
    lib_caps = requested_capabilities("What is in my library?")
    assert {"documents", "research", "skills"} <= set(lib_caps)
    lib_contract = resolve_turn_contract(
        capabilities=lib_caps,
        schemas=schemas,
        policy=policy,
        selected_tools=selected_tools_for_request("What is in my library?"),
        message="What is in my library?",
    )
    assert {"manage_documents", "manage_research", "manage_skills"} <= set(lib_contract.offered)
