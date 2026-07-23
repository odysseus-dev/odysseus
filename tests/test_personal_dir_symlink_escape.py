"""Regression tests for personal-directory path confinement."""
import ast
import asyncio
import os
from pathlib import Path

import pytest
from fastapi import HTTPException

from routes import personal_routes
from src.request_models import DirectoryRequest

SRC = Path(__file__).resolve().parent.parent / "routes" / "personal_routes.py"


def _function_source(src_text, name):
    tree = ast.parse(src_text)
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return ast.get_source_segment(src_text, node)
    raise AssertionError(f"{name} not found in {SRC}")


class _FakePersonalDocs:
    index = []

    def __init__(self):
        self.added = []
        self.removed = []

    def add_directory(self, directory, index=False):
        self.added.append((directory, index))

    def remove_directory(self, directory):
        self.removed.append(directory)


class _FakeRAG:
    def __init__(self):
        self.indexed = []
        self.removed = []

    def index_personal_documents(self, directory, owner=None):
        self.indexed.append((directory, owner))
        return {"success": True, "indexed_count": 0, "failed_count": 0}

    def remove_directory(self, directory):
        self.removed.append(directory)


def _endpoint(personal_docs, method, path):
    router = personal_routes.setup_personal_routes(personal_docs, None, True)
    for route in router.routes:
        if (
            getattr(route, "path", "") == path
            and method in getattr(route, "methods", set())
        ):
            return route.endpoint
    raise AssertionError(f"{method} {path} endpoint not found")


def _invoke(operation, directory, personal_docs):
    if operation == "add":
        endpoint = _endpoint(personal_docs, "POST", "/api/personal/add_directory")
        return asyncio.run(
            endpoint(
                request=object(),
                directory_request=DirectoryRequest(directory=directory),
                owner="alice",
                _admin=None,
            )
        )

    endpoint = _endpoint(personal_docs, "DELETE", "/api/personal/remove_directory")
    return asyncio.run(endpoint(directory=directory, owner="alice", _admin=None))


@pytest.mark.parametrize(
    "route_name",
    ["add_directory_to_rag", "remove_directory_from_rag"],
)
def test_confinement_is_inline_and_codeql_visible(route_name):
    body = _function_source(SRC.read_text(), route_name)
    assert "os.path.realpath" in body, (
        f"{route_name} must resolve symlinks before checking confinement"
    )
    assert "os.path.abspath" not in body
    assert "os.path.commonpath" not in body
    assert "os.path.normcase" not in body
    assert "if not directory.startswith(base_abs):" in body, (
        "the CodeQL-recognized safe-access guard must run on every accepted path"
    )
    assert body.index("if not directory.startswith(base_abs):") < body.index(
        "if directory != base_abs"
    ), "the broad analyzer guard must run before the separator-safe boundary check"


@pytest.mark.parametrize("operation", ["add", "remove"])
@pytest.mark.parametrize("directory_kind", ["root", "relative", "absolute"])
def test_confinement_accepts_root_and_descendants(
    operation, directory_kind, tmp_path, monkeypatch
):
    base = tmp_path / "PersonalDocs"
    nested = base / "nested"
    nested.mkdir(parents=True)
    monkeypatch.setattr(personal_routes, "PERSONAL_DIR", str(base))
    rag = _FakeRAG()
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)
    docs = _FakePersonalDocs()

    supplied = {
        "root": ".",
        "relative": "nested",
        "absolute": str(nested),
    }[directory_kind]
    expected = os.path.realpath(base if directory_kind == "root" else nested)
    result = _invoke(operation, supplied, docs)

    assert result["success"] is True
    assert result["directory"] == expected
    if operation == "add":
        assert docs.added == [(expected, False)]
        assert rag.indexed == [(expected, "alice")]
    else:
        assert docs.removed == [expected]
        assert rag.removed == [expected]


@pytest.mark.parametrize("operation", ["add", "remove"])
def test_confinement_rejects_parent_and_sibling_prefix(
    operation, tmp_path, monkeypatch
):
    base = tmp_path / "personal"
    base.mkdir()
    sibling = tmp_path / "personal-backup"
    sibling.mkdir()
    monkeypatch.setattr(personal_routes, "PERSONAL_DIR", str(base))
    rag = _FakeRAG()
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)

    for directory in ("..", str(sibling)):
        docs = _FakePersonalDocs()
        with pytest.raises(HTTPException) as exc:
            _invoke(operation, directory, docs)
        assert exc.value.status_code == 403
        assert docs.added == []
        assert docs.removed == []
        assert rag.indexed == []
        assert rag.removed == []


@pytest.mark.parametrize("operation", ["add", "remove"])
def test_confinement_rejects_symlink_escape(operation, tmp_path, monkeypatch):
    base = tmp_path / "personal"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = base / "escape"
    try:
        os.symlink(outside, link)
    except (AttributeError, NotImplementedError, OSError) as exc:
        pytest.skip(f"symlinks unavailable: {exc}")
    monkeypatch.setattr(personal_routes, "PERSONAL_DIR", str(base))
    rag = _FakeRAG()
    monkeypatch.setattr(personal_routes, "get_rag_manager", lambda: rag)
    docs = _FakePersonalDocs()

    with pytest.raises(HTTPException) as exc:
        _invoke(operation, str(link), docs)

    assert exc.value.status_code == 403
    assert docs.added == []
    assert docs.removed == []
    assert rag.indexed == []
    assert rag.removed == []
