import importlib.util
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def _load_module():
    path = ROOT / "scripts" / "migrate_faiss_to_chroma.py"
    spec = importlib.util.spec_from_file_location("migrate_faiss_to_chroma", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_memory_map_skips_invalid_rows():
    mod = _load_module()

    assert mod._memory_map([
        {"id": "m1", "text": "hello"},
        "bad-row",
        None,
        {"text": "missing id"},
    ]) == {"m1": {"id": "m1", "text": "hello"}}


def test_rag_docstore_requires_matching_lists():
    mod = _load_module()

    assert mod._rag_docstore([]) == ([], [], [])
    assert mod._rag_docstore({"ids": ["a"], "documents": ["doc"], "metadatas": "bad"}) == ([], [], [])
    assert mod._rag_docstore({
        "ids": ["a", "b"],
        "documents": ["doc"],
        "metadatas": [{"source": "x"}, {"source": "y"}],
    }) == (["a"], ["doc"], [{"source": "x"}])
