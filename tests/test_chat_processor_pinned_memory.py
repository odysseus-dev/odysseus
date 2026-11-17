from types import SimpleNamespace

from src.chat_processor import ChatProcessor


class _Memory:
    def __init__(self, rows):
        self.rows = rows
        self.incremented = []

    def load(self, owner=None):
        return list(self.rows)

    def increment_uses(self, ids):
        self.incremented.extend(ids)


class _Docs:
    rag_manager = None


def _context_text(preface):
    return "\n".join(m.get("content", "") for m in preface)


def _processor(rows):
    return ChatProcessor(memory_manager=_Memory(rows), personal_docs_manager=_Docs())


def test_pinned_memory_does_not_inject_every_unrelated_fact():
    rows = [
        {
            "id": "identity",
            "text": "User's name is Felix.",
            "category": "identity",
            "pinned": True,
            "timestamp": 3,
        },
        {
            "id": "party",
            "text": "User is planning a birthday party with sack races.",
            "category": "fact",
            "pinned": True,
            "timestamp": 2,
        },
        {
            "id": "coffee",
            "text": "User likes dark roast coffee.",
            "category": "preference",
            "pinned": True,
            "timestamp": 1,
        },
    ]

    preface, _, _ = _processor(rows).build_context_preface(
        message="Explain how Python decorators work",
        session=SimpleNamespace(),
        use_rag=False,
        use_memory=True,
    )

    text = _context_text(preface)
    assert "User's name is Felix." in text
    assert "birthday party with sack races" not in text
    assert "dark roast coffee" not in text


def test_relevant_pinned_memory_is_still_injected():
    rows = [
        {
            "id": "coffee",
            "text": "User likes dark roast coffee.",
            "category": "preference",
            "pinned": True,
            "timestamp": 1,
        },
        {
            "id": "party",
            "text": "User is planning a birthday party with sack races.",
            "category": "fact",
            "pinned": True,
            "timestamp": 2,
        },
    ]

    preface, _, _ = _processor(rows).build_context_preface(
        message="likes coffee roast",
        session=SimpleNamespace(),
        use_rag=False,
        use_memory=True,
    )

    text = _context_text(preface)
    assert "User likes dark roast coffee." in text
    assert "birthday party with sack races" not in text


def test_pinned_memory_injection_is_capped_at_five():
    rows = [
        {
            "id": f"identity-{idx}",
            "text": f"User identity fact {idx} email marker.",
            "category": "identity",
            "pinned": True,
            "timestamp": idx,
        }
        for idx in range(10)
    ]

    processor = _processor(rows)
    processor.build_context_preface(
        message="Who is the user?",
        session=SimpleNamespace(),
        use_rag=False,
        use_memory=True,
    )

    assert len(processor._last_used_memories) == 5


def test_pinned_identity_auto_injection_uses_first_five_not_newest():
    rows = [
        {
            "id": f"identity-{idx}",
            "text": f"User identity fact {idx} email marker.",
            "category": "identity",
            "pinned": True,
            "timestamp": idx,
        }
        for idx in range(10)
    ]

    processor = _processor(rows)
    processor.build_context_preface(
        message="Who is the user?",
        session=SimpleNamespace(),
        use_rag=False,
        use_memory=True,
    )

    assert [m["text"] for m in processor._last_used_memories] == [
        f"User identity fact {idx} email marker."
        for idx in range(5)
    ]


def test_total_memory_injection_is_capped_at_five_across_pinned_and_recalled():
    rows = [
        {
            "id": f"identity-{idx}",
            "text": f"User identity fact {idx} email marker.",
            "category": "identity",
            "pinned": True,
            "timestamp": idx,
        }
        for idx in range(4)
    ]
    rows.extend([
        {
            "id": f"coffee-{idx}",
            "text": f"User likes coffee roast {idx}.",
            "category": "preference",
            "pinned": False,
            "timestamp": idx,
        }
        for idx in range(6)
    ])

    processor = _processor(rows)
    processor.build_context_preface(
        message="likes coffee roast",
        session=SimpleNamespace(),
        use_rag=False,
        use_memory=True,
    )

    assert len(processor._last_used_memories) <= 5
    assert sum(1 for m in processor._last_used_memories if m["type"] == "pinned") == 4


def test_explicit_memory_management_skips_recall_injection():
    rows = [{
        "id": "identity",
        "text": "User's name is Felix.",
        "category": "identity",
        "pinned": True,
        "timestamp": 1,
    }]

    processor = _processor(rows)
    preface, _, _ = processor.build_context_preface(
        message="list my saved memories",
        session=SimpleNamespace(),
        use_rag=False,
        use_memory=True,
    )

    assert "User's name is Felix." not in _context_text(preface)
    assert processor._last_used_memories == []


def test_memory_management_detection_does_not_disable_normal_recall():
    assert ChatProcessor._is_memory_management_request("find my saved memories")
    assert ChatProcessor._is_memory_management_request("what do you remember about my nationality?")
    assert not ChatProcessor._is_memory_management_request("Explain how memory allocation works")
