from __future__ import annotations

import hashlib
import json
import shutil
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from odysseus_desktop_backend.logging_config import get_logger
from odysseus_desktop_backend.storage import Database, utc_ms


SUPPORTED_EXTENSIONS = {".txt", ".md", ".pdf"}
LOW_TEXT_MIN_CHARS = 80
logger = get_logger("documents")


@dataclass(frozen=True)
class ExtractedPage:
    page_number: int
    text: str
    extraction_method: str
    metadata: dict[str, Any]


@dataclass(frozen=True)
class OCRPage:
    source_path: str
    page_number: int
    engine_name: str
    confidence: float | None
    text: str


class DocumentService:
    def __init__(self, db: Database):
        self.db = db
        self.documents_dir = self.db.profile_dir / "files" / "documents"
        self.documents_dir.mkdir(parents=True, exist_ok=True)

    def import_document(self, path: str) -> dict[str, Any]:
        source = Path(path).expanduser().resolve()
        logger.info("document import requested path=%s", source)
        if not source.exists() or not source.is_file():
            raise FileNotFoundError(f"File not found: {path}")

        extension = source.suffix.lower()
        if extension not in SUPPORTED_EXTENSIONS:
            raise ValueError("Unsupported file type. Choose a .txt, .md, or extractable .pdf file.")

        document_id = str(uuid.uuid4())
        content = source.read_bytes()
        digest = hashlib.sha256(content).hexdigest()
        stored_path = self.documents_dir / f"{document_id}{extension}"
        shutil.copyfile(source, stored_path)

        now = utc_ms()
        title = source.stem.strip() or source.name
        file_type = extension.removeprefix(".")
        self.db.conn.execute(
            """
            INSERT INTO documents(
                id, title, source_path, stored_path, file_name, file_type, content_hash,
                size_bytes, status, index_status, is_deleted, is_low_text, error,
                created_at, updated_at, indexed_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, 'imported', 'pending', 0, 0, '', ?, ?, NULL)
            """,
            (
                document_id,
                title,
                str(source),
                str(stored_path),
                source.name,
                file_type,
                digest,
                len(content),
                now,
                now,
            ),
        )
        self.db.conn.commit()

        try:
            pages = self.extract_pages(stored_path)
            self.replace_pages(document_id, pages)
            total_text = sum(len(page.text.strip()) for page in pages)
            if extension == ".pdf" and total_text < LOW_TEXT_MIN_CHARS:
                self.mark_low_text(document_id)
                logger.info(
                    "document import low_text document_id=%s file_name=%s pages=%s text_chars=%s",
                    document_id,
                    source.name,
                    len(pages),
                    total_text,
                )
            else:
                logger.info(
                    "document import complete document_id=%s file_name=%s pages=%s text_chars=%s",
                    document_id,
                    source.name,
                    len(pages),
                    total_text,
                )
            return self.get(document_id)
        except Exception as exc:  # noqa: BLE001 - store import failure for UI/reporting
            self.mark_error(document_id, str(exc))
            logger.warning("document import failed document_id=%s path=%s error=%s", document_id, source, exc)
            raise

    def extract_pages(self, path: str | Path) -> list[ExtractedPage]:
        file_path = Path(path)
        extension = file_path.suffix.lower()
        if extension in {".txt", ".md"}:
            text = file_path.read_text(encoding="utf-8", errors="replace")
            return [
                ExtractedPage(
                    page_number=1,
                    text=text,
                    extraction_method="plain_text",
                    metadata={"file_type": extension.removeprefix(".")},
                )
            ]
        if extension == ".pdf":
            return self._extract_pdf_pages(file_path)
        raise ValueError("unsupported document type")

    def replace_pages(self, document_id: str, pages: list[ExtractedPage]) -> None:
        now = utc_ms()
        self.db.conn.execute("DELETE FROM document_pages WHERE document_id = ?", (document_id,))
        for page in pages:
            text = page.text or ""
            self.db.conn.execute(
                """
                INSERT INTO document_pages(
                    id, document_id, page_number, text, text_hash, extraction_method,
                    metadata_json, created_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    document_id,
                    page.page_number,
                    text,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    page.extraction_method,
                    json.dumps(page.metadata),
                    now,
                ),
            )
        self.db.conn.execute(
            """
            UPDATE documents
            SET updated_at = ?, error = ''
            WHERE id = ?
            """,
            (now, document_id),
        )
        self.db.conn.commit()

    def replace_pages_from_ocr(self, document_id: str, pages: list[OCRPage]) -> None:
        extracted = [
            ExtractedPage(
                page_number=page.page_number,
                text=page.text,
                extraction_method=f"ocr:{page.engine_name}",
                metadata={
                    "file_type": "pdf",
                    "ocr": True,
                    "engine_name": page.engine_name,
                    "confidence": page.confidence,
                },
            )
            for page in pages
        ]
        self.replace_pages(document_id, extracted)
        self.replace_ocr_pages(document_id, pages)

    def replace_ocr_pages(self, document_id: str, pages: list[OCRPage], index_status: str = "pending") -> None:
        now = utc_ms()
        self.db.conn.execute("DELETE FROM ocr_pages WHERE document_id = ?", (document_id,))
        for page in pages:
            text = page.text or ""
            self.db.conn.execute(
                """
                INSERT INTO ocr_pages(
                    id, document_id, source_path, page_number, engine_name, confidence,
                    text, text_hash, chunk_ids_json, index_status, created_at, updated_at
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, '[]', ?, ?, ?)
                """,
                (
                    str(uuid.uuid4()),
                    document_id,
                    page.source_path,
                    page.page_number,
                    page.engine_name,
                    page.confidence,
                    text,
                    hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    index_status,
                    now,
                    now,
                ),
            )
        self.db.conn.commit()

    def list(self) -> list[dict[str, Any]]:
        rows = self.db.conn.execute(
            """
            SELECT *
            FROM documents
            WHERE is_deleted = 0
            ORDER BY updated_at DESC
            """
        ).fetchall()
        return [self._document_dict(row) for row in rows]

    def get(self, document_id: str) -> dict[str, Any]:
        row = self.db.conn.execute(
            "SELECT * FROM documents WHERE id = ?",
            (document_id,),
        ).fetchone()
        if row is None:
            raise KeyError(f"document not found: {document_id}")
        return self._document_dict(row)

    def pages(self, document_id: str) -> list[dict[str, Any]]:
        self.get(document_id)
        rows = self.db.conn.execute(
            """
            SELECT id, document_id, page_number, text, text_hash, extraction_method,
                   metadata_json, created_at
            FROM document_pages
            WHERE document_id = ?
            ORDER BY page_number ASC
            """,
            (document_id,),
        ).fetchall()
        pages: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            pages.append(item)
        return pages

    def chunks(self, document_id: str) -> list[dict[str, Any]]:
        self.get(document_id)
        rows = self.db.conn.execute(
            """
            SELECT id, document_id, chunk_index, content, content_hash, page_start,
                   page_end, metadata_json, embedding_model, embedding_hash,
                   is_deleted, created_at, updated_at
            FROM rag_chunks
            WHERE document_id = ? AND is_deleted = 0
            ORDER BY chunk_index ASC
            """,
            (document_id,),
        ).fetchall()
        chunks: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["metadata"] = json.loads(item.pop("metadata_json") or "{}")
            chunks.append(item)
        return chunks

    def ocr_pages(self, document_id: str) -> list[dict[str, Any]]:
        self.get(document_id)
        rows = self.db.conn.execute(
            """
            SELECT id, document_id, source_path, page_number, engine_name, confidence,
                   text, text_hash, chunk_ids_json, index_status, created_at, updated_at
            FROM ocr_pages
            WHERE document_id = ?
            ORDER BY page_number ASC
            """,
            (document_id,),
        ).fetchall()
        pages: list[dict[str, Any]] = []
        for row in rows:
            item = dict(row)
            item["chunk_ids"] = json.loads(item.pop("chunk_ids_json") or "[]")
            pages.append(item)
        return pages

    def mark_indexing(self, document_id: str) -> None:
        self._update_status(document_id, index_status="indexing", status="imported", error="")

    def mark_indexed(self, document_id: str) -> None:
        now = utc_ms()
        self.db.conn.execute(
            """
            UPDATE documents
            SET index_status = 'indexed', status = 'indexed', error = '',
                updated_at = ?, indexed_at = ?
            WHERE id = ?
            """,
            (now, now, document_id),
        )
        self.db.conn.commit()

    def mark_low_text(self, document_id: str) -> None:
        self._update_status(
            document_id,
            status="low_text",
            index_status="low_text",
            is_low_text=1,
            ocr_status="needed",
            error="Low-text or scanned PDF detected. OCR is optional in Milestone 3.",
        )

    def mark_error(self, document_id: str, error: str) -> None:
        self._update_status(document_id, status="error", index_status="error", error=error)

    def mark_ocr_ready(self, document_id: str, engine_name: str) -> None:
        self._update_status(
            document_id,
            status="ocr_ready",
            index_status="pending",
            is_low_text=0,
            ocr_status="indexing",
            ocr_engine=engine_name,
            ocr_error="",
            error="",
        )

    def mark_ocr_running(self, document_id: str, engine_name: str) -> None:
        self._update_status(
            document_id,
            ocr_status="running",
            ocr_engine=engine_name,
            ocr_error="",
            error="",
        )

    def mark_ocr_unavailable(self, document_id: str, reason: str) -> None:
        self._update_status(
            document_id,
            ocr_status="unavailable",
            ocr_error=reason,
            error=reason,
        )

    def mark_ocr_indexed(self, document_id: str, engine_name: str) -> None:
        self._update_status(
            document_id,
            status="ocr_indexed",
            index_status="indexed",
            is_low_text=0,
            ocr_status="indexed",
            ocr_engine=engine_name,
            ocr_error="",
            error="",
        )

    def link_ocr_chunks(self, document_id: str) -> None:
        chunks = self.chunks(document_id)
        now = utc_ms()
        for page in self.ocr_pages(document_id):
            page_number = page["page_number"]
            chunk_ids = [
                chunk["id"]
                for chunk in chunks
                if (chunk["page_start"] or 0) <= page_number <= (chunk["page_end"] or page_number)
            ]
            self.db.conn.execute(
                """
                UPDATE ocr_pages
                SET chunk_ids_json = ?, index_status = ?, updated_at = ?
                WHERE id = ?
                """,
                (
                    json.dumps(chunk_ids),
                    "indexed" if chunk_ids else "pending",
                    now,
                    page["id"],
                ),
            )
        self.db.conn.commit()

    def mark_deleted(self, document_id: str) -> dict[str, Any]:
        now = utc_ms()
        self.db.conn.execute(
            """
            UPDATE documents
            SET is_deleted = 1, status = 'deleted', index_status = 'deleted', updated_at = ?
            WHERE id = ?
            """,
            (now, document_id),
        )
        self.db.conn.commit()
        logger.info("document deleted document_id=%s", document_id)
        return {"deleted": True, "document_id": document_id}

    def _extract_pdf_pages(self, path: Path) -> list[ExtractedPage]:
        try:
            from pypdf import PdfReader
        except ImportError as exc:
            raise RuntimeError(
                "PDF text extraction requires pypdf. Install python/requirements.txt before importing PDFs."
            ) from exc

        reader = PdfReader(str(path))
        pages: list[ExtractedPage] = []
        for index, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            pages.append(
                ExtractedPage(
                    page_number=index,
                    text=text,
                    extraction_method="pdf_text",
                    metadata={"file_type": "pdf"},
                )
            )
        return pages

    def _update_status(self, document_id: str, **values: Any) -> None:
        allowed = {
            "status",
            "index_status",
            "is_low_text",
            "error",
            "ocr_status",
            "ocr_engine",
            "ocr_error",
        }
        fields = {key: value for key, value in values.items() if key in allowed}
        if not fields:
            return
        fields["updated_at"] = utc_ms()
        assignments = ", ".join(f"{key} = ?" for key in fields)
        self.db.conn.execute(
            f"UPDATE documents SET {assignments} WHERE id = ?",
            [*fields.values(), document_id],
        )
        self.db.conn.commit()

    def _document_dict(self, row: Any) -> dict[str, Any]:
        item = dict(row)
        item["is_deleted"] = bool(item["is_deleted"])
        item["is_low_text"] = bool(item["is_low_text"])
        return item
