import asyncio

from services.docs.service import DocsService


class _FakeRag:
    """Stands in for RAGManager.search. A corrupt or stale Chroma index can
    return a non-dict row alongside the well-formed ones."""

    def search(self, query, k=5):
        return [
            {"text": "alpha", "source": "a.txt", "score": 0.9},
            "corrupt-row",
            None,
        ]


def test_query_skips_non_dict_rag_rows():
    # Bypass __init__ (it builds a real RAGManager / Chroma client) and inject
    # a fake search backend.
    svc = DocsService.__new__(DocsService)
    svc.rag = _FakeRag()
    out = asyncio.run(svc.query("anything"))
    # old code called r.get(...) on the str/None rows and raised AttributeError.
    assert [c.text for c in out] == ["alpha"]
    assert out[0].source == "a.txt"
