"""Tests for RAG synchronization during document CRUD operations."""

import pytest
from unittest.mock import MagicMock, patch
from types import SimpleNamespace
import os

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import NullPool

import core.database as cdb
import routes.document_routes as droutes
from routes.document_helpers import DocumentCreate, DocumentUpdate

# Setup temporary sqlite database for tests
import tempfile
_TMPDB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_ENGINE = create_engine(
    f"sqlite:///{_TMPDB.name}",
    connect_args={"check_same_thread": False},
    poolclass=NullPool,
)
cdb.Base.metadata.create_all(_ENGINE)
_TS = sessionmaker(bind=_ENGINE, autoflush=False, autocommit=False)


def _req(user="alice"):
    req = SimpleNamespace()
    req.state = SimpleNamespace(current_user=user)
    req.client = SimpleNamespace(host="127.0.0.1")
    auth_manager = MagicMock()
    auth_manager.privilege_gated = False
    req.app = SimpleNamespace(state=SimpleNamespace(auth_manager=auth_manager))
    return req


def _endpoint(method, path):
    router = droutes.setup_document_routes(MagicMock(), MagicMock())
    for route in router.routes:
        if getattr(route, "path", None) == path and method in getattr(route, "methods", set()):
            return route.endpoint
    raise RuntimeError(f"{method} {path} not found")


@pytest.mark.asyncio
async def test_document_routes_rag_sync_lifecycle():
    previous_session_local = droutes.SessionLocal
    droutes.SessionLocal = _TS
    
    mock_rag = MagicMock()
    mock_rag.healthy = True
    mock_rag._split_into_chunks.side_effect = lambda text, *args, **kwargs: [text]
    mock_rag.add_document.return_value = True
    mock_rag.delete_by_source.return_value = 1

    try:
        with patch("src.rag_singleton.get_rag_manager", return_value=mock_rag):
            # 1. Create document
            create_endpoint = _endpoint("POST", "/api/document")
            doc_create = DocumentCreate(title="Test Doc", content="Hello RAG world!", language="markdown")
            
            mock_rag.add_document.reset_mock()
            mock_rag.delete_by_source.reset_mock()
            
            doc_res = await create_endpoint(_req("alice"), doc_create)
            doc_id = doc_res["id"]
            
            mock_rag.delete_by_source.assert_called_once_with(f"document:{doc_id}")
            mock_rag.add_document.assert_called_once()
            assert mock_rag.add_document.call_args[0][0] == "Hello RAG world!"
            metadata = mock_rag.add_document.call_args[0][1]
            assert metadata["source"] == f"document:{doc_id}"
            assert metadata["filename"] == "Test Doc"
            assert metadata["owner"] == "alice"

            # 2. Update document
            update_endpoint = _endpoint("PUT", "/api/document/{doc_id}")
            doc_update = DocumentUpdate(content="Updated content!", summary="Edit")
            
            mock_rag.add_document.reset_mock()
            mock_rag.delete_by_source.reset_mock()
            
            await update_endpoint(_req("alice"), doc_id, doc_update)
            
            mock_rag.delete_by_source.assert_called_once_with(f"document:{doc_id}")
            mock_rag.add_document.assert_called_once()
            assert mock_rag.add_document.call_args[0][0] == "Updated content!"

            # 3. Delete document
            delete_endpoint = _endpoint("DELETE", "/api/document/{doc_id}")
            
            mock_rag.delete_by_source.reset_mock()
            
            await delete_endpoint(_req("alice"), doc_id)
            
            mock_rag.delete_by_source.assert_called_once_with(f"document:{doc_id}")
    finally:
        droutes.SessionLocal = previous_session_local
        try:
            os.unlink(_TMPDB.name)
        except OSError:
            pass
