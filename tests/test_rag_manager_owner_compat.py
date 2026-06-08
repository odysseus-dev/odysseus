from src.rag_manager import RAGManager


class _FakeVectorRAG:
    def __init__(self):
        self.calls = []

    def index_personal_documents(self, directory, file_extensions=None, owner=None):
        self.calls.append(
            {
                "directory": directory,
                "file_extensions": file_extensions,
                "owner": owner,
            }
        )
        return {"success": True, "indexed_count": 1}


def test_rag_manager_forwards_owner_and_file_extensions():
    fake = _FakeVectorRAG()
    manager = RAGManager.__new__(RAGManager)
    manager.vector_rag = fake
    extensions = {".md", ".txt"}

    result = manager.index_personal_documents(
        "/tmp/personal",
        file_extensions=extensions,
        owner="alice",
    )

    assert result == {"success": True, "indexed_count": 1}
    assert fake.calls == [
        {
            "directory": "/tmp/personal",
            "file_extensions": extensions,
            "owner": "alice",
        }
    ]
