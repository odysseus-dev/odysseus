import asyncio

from services.docs.service import DocsService


class _FakeRag:
    """Stands in for RAGManager.search. A corrupt or stale Chroma index can
    return a non-dict row alongside the well-formed ones."""

    def search(self, query, k=5):
        return [
            {
                "document": "alpha",
                "metadata": {"source": "a.txt"},
                "similarity": 0.9,
            },
            "corrupt-row",
            None,
        ]

    def index_personal_documents(self, directory):
        return {"indexed_count": 7, "failed_count": 2, "errors": ["bad.pdf"]}


def test_query_skips_non_dict_rag_rows():
    # Bypass __init__ (it builds a real RAGManager / Chroma client) and inject
    # a fake search backend.
    svc = DocsService.__new__(DocsService)
    svc.rag = _FakeRag()
    out = asyncio.run(svc.query("anything"))
    # old code called r.get(...) on the str/None rows and raised AttributeError.
    assert [c.text for c in out] == ["alpha"]
    assert out[0].source == "a.txt"
    assert out[0].score == 0.9


def test_index_maps_live_vectorrag_result_shape():
    svc = DocsService.__new__(DocsService)
    svc.rag = _FakeRag()

    out = asyncio.run(svc.index("/documents"))

    assert out.indexed == 7
    assert out.failed == 2
    assert out.errors == ["bad.pdf"]
