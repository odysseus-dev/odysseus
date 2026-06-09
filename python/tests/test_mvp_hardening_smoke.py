from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import odysseus_desktop_backend.services.model_service as model_module
from odysseus_desktop_backend.logging_config import setup_logging
from odysseus_desktop_backend.services.chat_service import ChatService
from odysseus_desktop_backend.services.document_service import DocumentService, ExtractedPage, OCRPage
from odysseus_desktop_backend.services.embedding_service import EmbeddingService
from odysseus_desktop_backend.services.legacy_import_service import LegacyImportService
from odysseus_desktop_backend.services.model_service import ModelService
from odysseus_desktop_backend.services.ocr_service import OCREngineStatus, OCRService
from odysseus_desktop_backend.services.rag_service import RAGService
from odysseus_desktop_backend.services.session_service import SessionService
from odysseus_desktop_backend.services.settings_service import SettingsService
from odysseus_desktop_backend.services.vector_store import SQLiteNumPyVectorStore
from odysseus_desktop_backend.storage import Database
from rpc_server import SidecarApp


class FakeModelService(ModelService):
    def chat(self, _model: str, messages: list[dict[str, str]]) -> str:
        context = messages[0]["content"] if messages and messages[0]["role"] == "system" else ""
        return f"fake assistant reply\n{context[:120]}".strip()


class AvailableOcrEngine:
    name = "mock-ocr"

    def status(self):
        return OCREngineStatus(True, self.name, "mock-renderer", "OCR is available.")

    def ocr_pdf(self, _stored_path: str, source_path: str):
        return [
            OCRPage(
                source_path=source_path,
                page_number=1,
                engine_name=self.name,
                confidence=91.0,
                text="Scanned invoice states that public water notices must be mailed by Friday.",
            )
        ]


def build_services(profile_dir: Path):
    db = Database(profile_dir)
    documents = DocumentService(db)
    embeddings = EmbeddingService(db)
    rag = RAGService(documents, embeddings, SQLiteNumPyVectorStore(db))
    sessions = SessionService(db)
    settings = SettingsService(db)
    return db, documents, rag, sessions, settings


def test_fresh_sidecar_profile_logs_and_document_rag_smoke(tmp_path: Path):
    profile = tmp_path / "profile"
    setup_logging(profile)
    source = tmp_path / "source.txt"
    source.write_text("The MVP smoke document explains local RAG restart persistence.", encoding="utf-8")

    app = SidecarApp(profile)
    try:
        health = app.dispatch("health.ping", {})
        imported = app.dispatch("documents.import", {"path": str(source)})
        results = app.dispatch("rag.search", {"query": "restart persistence", "limit": 1})
        app.dispatch("app.shutdown", {})
    finally:
        app.close()

    assert health["ok"] is True
    assert imported["document"]["index_status"] == "indexed"
    assert results[0]["document_id"] == imported["document"]["id"]
    log_text = (profile / "logs" / "backend.log").read_text(encoding="utf-8")
    assert "backend startup" in log_text
    assert "document import" in log_text

    reopened = SidecarApp(profile)
    try:
        restarted = reopened.dispatch("rag.search", {"query": "local RAG", "limit": 1})
        assert restarted[0]["document_id"] == imported["document"]["id"]
    finally:
        reopened.close()


def test_ollama_missing_and_reachable_detection_smoke(tmp_path: Path, monkeypatch):
    db = Database(tmp_path / "profile")
    service = ModelService(db)

    monkeypatch.setattr(model_module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(service, "_tcp_reachable", lambda _host, _port: False)
    missing = service.detect_ollama()
    assert missing["installed"] is False
    assert missing["reachable"] is False

    monkeypatch.setattr(model_module.shutil, "which", lambda _name: "ollama")
    monkeypatch.setattr(service, "_tcp_reachable", lambda _host, _port: True)
    monkeypatch.setattr(
        service,
        "_get_json",
        lambda url, timeout: {"models": [{"name": "llama3.2"}]} if url.endswith("/api/tags") else {"version": "test"},
    )
    reachable = service.detect_ollama()
    assert reachable["installed"] is True
    assert reachable["reachable"] is True
    assert reachable["models"] == ["llama3.2"]
    db.close()


def test_basic_chat_rag_chat_ocr_and_legacy_smoke(tmp_path: Path, monkeypatch):
    profile = tmp_path / "profile"
    db, documents, rag, sessions, settings = build_services(profile)
    chat = ChatService(sessions, settings, FakeModelService(db), rag=rag)

    simple = chat.send("hello without RAG")
    assert simple["retrieved_chunks"] == []

    source = tmp_path / "rag.md"
    source.write_text("RAG smoke source mentions civic notices and mailed water bills.", encoding="utf-8")
    document = documents.import_document(str(source))
    rag.index_document(document["id"])
    rag_chat = chat.send("What does the smoke source mention?", use_rag=True)
    assert rag_chat["retrieved_chunks"]
    assert rag_chat["retrieved_chunks"][0]["metadata"]["file_name"] == "rag.md"

    scan = tmp_path / "scan.pdf"
    scan.write_bytes(b"%PDF-1.4\n% scanned placeholder")
    monkeypatch.setattr(
        DocumentService,
        "_extract_pdf_pages",
        lambda _self, _path: [
            ExtractedPage(page_number=1, text="", extraction_method="pdf_text", metadata={"file_type": "pdf"})
        ],
    )
    scanned = documents.import_document(str(scan))
    ocr = OCRService(documents, rag, engine=AvailableOcrEngine())
    ocr_result = ocr.run_document_ocr(scanned["id"])
    assert ocr_result["document"]["ocr_status"] == "indexed"
    assert rag.search("public water notices", limit=1)[0]["document_id"] == scanned["id"]

    legacy_root = tmp_path / "legacy"
    data = legacy_root / "data"
    data.mkdir(parents=True)
    (data / "settings.json").write_text(json.dumps({"default_model": "legacy-model"}), encoding="utf-8")
    legacy_db = data / "app.db"
    conn = sqlite3.connect(legacy_db)
    conn.executescript(
        """
        CREATE TABLE sessions(id TEXT PRIMARY KEY, title TEXT, model TEXT);
        CREATE TABLE chat_messages(id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, created_at INTEGER);
        """
    )
    conn.execute("INSERT INTO sessions(id, title, model) VALUES ('s1', 'Legacy smoke', 'llama3.2')")
    conn.commit()
    conn.close()
    before = legacy_db.read_bytes()

    report = LegacyImportService(documents, rag, sessions, settings).import_folder(str(legacy_root))
    assert set(report) == {"imported", "skipped", "incompatible", "failed"}
    assert any(item["type"] == "settings" for item in report["imported"])
    assert legacy_db.read_bytes() == before
    db.close()

    reopened_db, reopened_documents, reopened_rag, _sessions, reopened_settings = build_services(profile)
    assert reopened_settings.get()["default_model"] == "legacy-model"
    assert reopened_documents.ocr_pages(scanned["id"])[0]["chunk_ids"]
    assert reopened_rag.search("mailed water bills", limit=1)[0]["document_id"] == document["id"]
    assert reopened_rag.search("public water notices", limit=1)[0]["document_id"] == scanned["id"]
    reopened_db.close()
