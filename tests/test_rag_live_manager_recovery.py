from src import ai_interaction
from src import personal_docs


class _RecoveredRag:
    def search(self, query, k=5, owner=None):
        return []


def test_personal_docs_manager_adopts_recovered_singleton(monkeypatch):
    recovered = _RecoveredRag()
    manager = personal_docs.PersonalDocsManager.__new__(personal_docs.PersonalDocsManager)
    manager.rag_manager = None
    monkeypatch.setattr("src.rag_singleton.get_rag_manager", lambda: recovered)

    assert manager.get_rag_manager() is recovered
    assert manager.rag_manager is recovered


def test_ai_interaction_uses_shared_recovered_manager(monkeypatch):
    recovered = _RecoveredRag()
    personal_manager = type(
        "PersonalManager",
        (),
        {"get_rag_manager": lambda self: recovered},
    )()
    monkeypatch.setattr(ai_interaction, "_rag_manager", None)
    monkeypatch.setattr(ai_interaction, "_personal_docs_manager", personal_manager)

    assert ai_interaction._get_live_rag_manager() is recovered
    assert ai_interaction._rag_manager is recovered
