from types import SimpleNamespace

from src.agent_loop import (
    _looks_like_memory_identity_turn,
    _minimal_saved_memory_message,
)
from src.chat_processor import ChatProcessor


def test_general_what_is_my_question_detects_memory_turn():
    assert _looks_like_memory_identity_turn(
        "What is my BatServer setup priority?"
    )


def test_unrelated_what_is_question_is_not_memory_turn():
    assert not _looks_like_memory_identity_turn(
        "What is the capital of France?"
    )


def test_minimal_saved_memory_prompt_requires_grounded_answer():
    messages = [
        {
            "role": "user",
            "content": (
                "Source: saved memory: retrieved context\n"
                "Memory context. Do not reference unless the user asks "
                "about these topics.\n"
                "- My BatServer setup priority is privacy-first local AI "
                "with no cloud dependency."
            ),
            "metadata": {"source": "saved memory: retrieved context"},
        }
    ]

    result = _minimal_saved_memory_message(messages)

    assert result is not None
    assert "Answer only from these facts" in result["content"]
    assert "Do not infer" in result["content"]
    assert "My BatServer setup priority" in result["content"]


def test_hybrid_retrieve_uses_session_id_metadata():
    processor = object.__new__(ChatProcessor)
    processor.memory_vector = None

    memories = [
        {
            "id": "privacy",
            "text": "User prioritizes privacy-first local AI with no cloud dependency",
            "session_id": "BatServer Setup Priority",
            "category": "preference",
            "timestamp": 1,
        },
        {
            "id": "gaming",
            "text": "BatComputer will focus on gaming and AI workloads.",
            "session_id": "Future NAS Document Review",
            "category": "project",
            "timestamp": 2,
        },
    ]

    results = processor._hybrid_retrieve(
        "What is my BatServer setup priority?",
        memories,
        k=2,
    )

    assert results
    assert results[0]["id"] == "privacy"
