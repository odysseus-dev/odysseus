from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from odysseus_desktop_backend.services.document_service import (
    DocumentService,
    ExtractedPage,
    OCRPage,
)
from odysseus_desktop_backend.services.embedding_service import EmbeddingService
from odysseus_desktop_backend.services.legacy_import_service import LegacyImportService
import odysseus_desktop_backend.services.ocr_service as ocr_module
from odysseus_desktop_backend.services.ocr_service import (
    OCRExecutionError,
    OCREngineStatus,
    OCRService,
    TesseractPdfEngine,
)
from odysseus_desktop_backend.services.rag_service import RAGService
from odysseus_desktop_backend.services.session_service import SessionService
from odysseus_desktop_backend.services.settings_service import SettingsService
from odysseus_desktop_backend.services.vector_store import SQLiteNumPyVectorStore
from odysseus_desktop_backend.storage import Database


class UnavailableEngine:
    name = "mock-ocr"

    def status(self):
        return OCREngineStatus(False, self.name, "", "This appears scanned/low-text. OCR is not installed/enabled yet.")

    def ocr_pdf(self, _stored_path: str, _source_path: str):
        raise AssertionError("unavailable OCR engine should not run")


class AvailableEngine:
    name = "mock-ocr"

    def status(self):
        return OCREngineStatus(True, self.name, "mock-renderer", "OCR is available.")

    def ocr_pdf(self, stored_path: str, source_path: str):
        return [
            OCRPage(
                source_path=source_path,
                page_number=1,
                engine_name=self.name,
                confidence=93.5,
                text="Scanned contract contains renewal clauses and invoice payment terms.",
            )
        ]


class NoTextEngine:
    name = "mock-ocr"

    def status(self):
        return OCREngineStatus(True, self.name, "mock-renderer", "OCR is available.")

    def ocr_pdf(self, stored_path: str, source_path: str):
        return [
            OCRPage(
                source_path=source_path,
                page_number=1,
                engine_name=self.name,
                confidence=None,
                text="",
            )
        ]


class FailingEngine:
    name = "mock-ocr"

    def status(self):
        return OCREngineStatus(True, self.name, "mock-renderer", "OCR is available.")

    def ocr_pdf(self, _stored_path: str, _source_path: str):
        raise OCRExecutionError("Tesseract OCR failed: Error opening data file")


def build_services(profile_dir: Path):
    db = Database(profile_dir)
    documents = DocumentService(db)
    embeddings = EmbeddingService(db)
    rag = RAGService(documents, embeddings, SQLiteNumPyVectorStore(db))
    return db, documents, rag


def import_low_text_pdf(tmp_path: Path, documents: DocumentService, monkeypatch) -> dict:
    pdf = tmp_path / "scan.pdf"
    pdf.write_bytes(b"%PDF-1.4\n% scanned placeholder")
    monkeypatch.setattr(
        DocumentService,
        "_extract_pdf_pages",
        lambda _self, _path: [
            ExtractedPage(
                page_number=1,
                text="",
                extraction_method="pdf_text",
                metadata={"file_type": "pdf"},
            )
        ],
    )
    return documents.import_document(str(pdf))


def test_tesseract_detection_uses_windows_fallback_when_path_is_stale(tmp_path: Path, monkeypatch):
    tesseract = tmp_path / "Tesseract-OCR" / "tesseract.exe"
    pdftoppm = tmp_path / "poppler" / "Library" / "bin" / "pdftoppm.exe"
    tesseract.parent.mkdir(parents=True)
    pdftoppm.parent.mkdir(parents=True)
    tesseract.write_text("", encoding="utf-8")
    pdftoppm.write_text("", encoding="utf-8")

    monkeypatch.setattr(ocr_module.shutil, "which", lambda _name: None)

    def fake_glob(pattern: str, recursive: bool = False):
        if pattern.endswith("tesseract.exe"):
            return [str(tesseract)]
        if pattern.endswith("pdftoppm.exe"):
            return [str(pdftoppm)]
        return []

    monkeypatch.setattr(ocr_module, "glob", fake_glob)

    status = TesseractPdfEngine().status()

    assert status.available is True
    assert status.renderer == "pdftoppm"
    assert status.dependencies["tesseract"].found is True
    assert status.dependencies["tesseract"].source == "windows_fallback"
    assert status.dependencies["pdftoppm"].found is True
    assert status.dependencies["mutool"].found is False


def test_tesseract_subprocess_uses_utf8_replacement_and_handles_none_stdout(monkeypatch):
    engine = TesseractPdfEngine()
    engine.tesseract = "tesseract"

    def fake_run(command, **kwargs):
        assert command[0] == "tesseract"
        assert kwargs["stdout"] == ocr_module.subprocess.PIPE
        assert kwargs["stderr"] == ocr_module.subprocess.PIPE
        assert kwargs["text"] is True
        assert kwargs["encoding"] == "utf-8"
        assert kwargs["errors"] == "replace"
        return ocr_module.subprocess.CompletedProcess(command, 0, stdout=None, stderr=None)

    monkeypatch.setattr(ocr_module.subprocess, "run", fake_run)

    text, confidence = engine._run_tesseract(Path("page.png"))

    assert text == ""
    assert confidence is None


def test_tesseract_nonzero_surfaces_stderr_without_decode_crash(monkeypatch):
    engine = TesseractPdfEngine()
    engine.tesseract = "tesseract"

    def fake_run(command, **_kwargs):
        return ocr_module.subprocess.CompletedProcess(
            command,
            1,
            stdout=None,
            stderr=b"Error opening data file \xff",
        )

    monkeypatch.setattr(ocr_module.subprocess, "run", fake_run)

    with pytest.raises(OCRExecutionError, match="Error opening data file"):
        engine._run_tesseract(Path("page.png"))


def test_ocr_unavailable_path_marks_document(tmp_path: Path, monkeypatch):
    db, documents, rag = build_services(tmp_path / "profile")
    document = import_low_text_pdf(tmp_path, documents, monkeypatch)
    ocr = OCRService(documents, rag, engine=UnavailableEngine())

    result = ocr.run_document_ocr(document["id"])

    assert result["index"] is None
    assert result["ocr_status"]["available"] is False
    assert result["stats"]["pages_processed"] == 0
    assert result["stats"]["warning"]
    assert result["document"]["ocr_status"] == "unavailable"
    assert "OCR is not installed/enabled" in result["document"]["ocr_error"]
    db.close()


def test_ocr_no_text_returns_clear_empty_result(tmp_path: Path, monkeypatch):
    db, documents, rag = build_services(tmp_path / "profile")
    document = import_low_text_pdf(tmp_path, documents, monkeypatch)
    ocr = OCRService(documents, rag, engine=NoTextEngine())

    result = ocr.run_document_ocr(document["id"])

    assert result["index"] is None
    assert len(result["ocr_pages"]) == 1
    assert result["ocr_pages"][0]["index_status"] == "no_text"
    assert result["stats"] == {
        "pages_processed": 1,
        "pages_with_text": 0,
        "chunks_created": 0,
        "embeddings_created": 0,
        "embeddings_cached": 0,
        "warning": "OCR ran, but no text was extracted.",
    }
    assert result["document"]["ocr_status"] == "unavailable"
    assert result["document"]["ocr_error"] == "OCR ran, but no text was extracted."
    db.close()


def test_ocr_execution_failure_returns_ui_error_without_raising(tmp_path: Path, monkeypatch):
    db, documents, rag = build_services(tmp_path / "profile")
    document = import_low_text_pdf(tmp_path, documents, monkeypatch)
    ocr = OCRService(documents, rag, engine=FailingEngine())

    result = ocr.run_document_ocr(document["id"])

    assert result["index"] is None
    assert result["ocr_pages"] == []
    assert result["stats"]["pages_processed"] == 0
    assert "Error opening data file" in result["stats"]["warning"]
    assert result["document"]["ocr_status"] == "unavailable"
    assert "Error opening data file" in result["document"]["ocr_error"]
    db.close()


def test_ocr_available_indexes_text_and_persists_restart(tmp_path: Path, monkeypatch):
    profile = tmp_path / "profile"
    db, documents, rag = build_services(profile)
    document = import_low_text_pdf(tmp_path, documents, monkeypatch)
    ocr = OCRService(documents, rag, engine=AvailableEngine())

    result = ocr.run_document_ocr(document["id"])

    assert result["document"]["ocr_status"] == "indexed"
    assert result["ocr_pages"][0]["confidence"] == 93.5
    assert result["ocr_pages"][0]["chunk_ids"]
    assert result["index"]["chunks"]
    assert result["stats"]["pages_processed"] == 1
    assert result["stats"]["pages_with_text"] == 1
    assert result["stats"]["chunks_created"] == len(result["index"]["chunks"])
    assert result["stats"]["embeddings_created"] == result["index"]["embedded"]
    assert result["stats"]["embeddings_cached"] == result["index"]["cached"]
    assert rag.search("renewal invoice payment", limit=1)[0]["document_id"] == document["id"]

    rerun = ocr.run_document_ocr(document["id"])
    assert rerun["stats"]["embeddings_created"] == 0
    assert rerun["stats"]["embeddings_cached"] == len(rerun["index"]["chunks"])
    db.close()

    reopened_db, reopened_documents, reopened_rag = build_services(profile)
    reopened_document = reopened_documents.get(document["id"])
    reopened_pages = reopened_documents.ocr_pages(document["id"])
    reopened_chunks = reopened_documents.chunks(document["id"])
    assert reopened_document["ocr_status"] == "indexed"
    assert reopened_pages[0]["chunk_ids"]
    assert reopened_chunks
    assert reopened_rag.health()["chunks"] >= len(reopened_chunks)
    assert reopened_rag.health()["cached_embeddings"] >= len(reopened_chunks)
    assert reopened_rag.search("contract renewal clauses", limit=1)[0]["document_id"] == document["id"]
    reopened_db.close()


def test_legacy_import_is_non_destructive_and_reports(tmp_path: Path):
    legacy_root = tmp_path / "legacy"
    data = legacy_root / "data"
    personal_docs = data / "personal_docs"
    personal_docs.mkdir(parents=True)
    source_doc = personal_docs / "source.txt"
    source_doc.write_text("Legacy RAG source about solar panels and battery storage.", encoding="utf-8")
    (personal_docs / "image.png").write_bytes(b"unsupported")
    (data / "settings.json").write_text(json.dumps({"default_model": "llama3.2:legacy", "unknown": True}), encoding="utf-8")
    (data / "memory.json").write_text(json.dumps([{"text": "User likes quiet local AI workspaces."}]), encoding="utf-8")

    db_path = data / "app.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE sessions(id TEXT PRIMARY KEY, name TEXT, model TEXT);
        CREATE TABLE chat_messages(id TEXT PRIMARY KEY, session_id TEXT, role TEXT, content TEXT, timestamp INTEGER);
        CREATE TABLE documents(id TEXT PRIMARY KEY, title TEXT, current_content TEXT);
        CREATE TABLE notes(id TEXT PRIMARY KEY, title TEXT);
        """
    )
    conn.execute("INSERT INTO sessions(id, name, model) VALUES ('s1', 'Legacy chat', 'llama3.2')")
    conn.execute(
        "INSERT INTO chat_messages(id, session_id, role, content, timestamp) VALUES ('m1', 's1', 'user', 'hello legacy', 1)"
    )
    conn.execute(
        "INSERT INTO chat_messages(id, session_id, role, content, timestamp) VALUES ('m2', 's1', 'assistant', 'hello imported', 2)"
    )
    conn.execute(
        "INSERT INTO documents(id, title, current_content) VALUES ('d1', 'Legacy Doc', 'Legacy document text about archive import reports.')"
    )
    conn.execute("INSERT INTO notes(id, title) VALUES ('n1', 'Bad note')")
    conn.commit()
    conn.close()
    before_db = db_path.read_bytes()
    before_doc = source_doc.read_text(encoding="utf-8")

    profile_db = Database(tmp_path / "profile")
    documents = DocumentService(profile_db)
    rag = RAGService(documents, EmbeddingService(profile_db), SQLiteNumPyVectorStore(profile_db))
    sessions = SessionService(profile_db)
    settings = SettingsService(profile_db)
    existing_doc_path = tmp_path / "existing-profile-doc.md"
    existing_doc_path.write_text("Existing profile document about basalt archives.", encoding="utf-8")
    existing_document = documents.import_document(str(existing_doc_path))
    rag.index_document(existing_document["id"])
    importer = LegacyImportService(documents, rag, sessions, settings)

    report = importer.import_folder(str(legacy_root))

    assert set(report) == {"imported", "skipped", "incompatible", "failed"}
    assert db_path.read_bytes() == before_db
    assert source_doc.read_text(encoding="utf-8") == before_doc
    assert documents.get(existing_document["id"])["is_deleted"] is False
    assert rag.search("basalt archives", limit=1)[0]["document_id"] == existing_document["id"]
    assert settings.get()["default_model"] == "llama3.2:legacy"
    assert sessions.list()[0]["title"] == "Legacy chat"
    assert rag.search("solar battery storage", limit=1)
    assert rag.search("archive import reports", limit=1)
    assert any(item["type"] == "notes" for item in report["incompatible"])
    assert any(item["type"] == "rag_source" for item in report["skipped"])
    assert report["failed"] == []
    profile_db.close()

    reopened_db = Database(tmp_path / "profile")
    reopened_documents = DocumentService(reopened_db)
    reopened_rag = RAGService(
        reopened_documents,
        EmbeddingService(reopened_db),
        SQLiteNumPyVectorStore(reopened_db),
    )
    reopened_sessions = SessionService(reopened_db)
    reopened_settings = SettingsService(reopened_db)

    assert reopened_documents.get(existing_document["id"])["is_deleted"] is False
    assert reopened_settings.get()["default_model"] == "llama3.2:legacy"
    assert reopened_sessions.list()[0]["title"] == "Legacy chat"
    assert reopened_rag.search("solar battery storage", limit=1)
    assert reopened_rag.search("basalt archives", limit=1)[0]["document_id"] == existing_document["id"]
    reopened_db.close()
