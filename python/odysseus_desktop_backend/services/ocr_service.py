from __future__ import annotations

import shutil
import subprocess
import tempfile
import os
from dataclasses import dataclass, field
from glob import glob
from pathlib import Path
from typing import Protocol

from odysseus_desktop_backend.logging_config import get_logger
from odysseus_desktop_backend.services.document_service import DocumentService, OCRPage
from odysseus_desktop_backend.services.rag_service import RAGService


OCR_UNAVAILABLE_MESSAGE = "This appears scanned/low-text. OCR is not installed/enabled yet."
logger = get_logger("ocr")


@dataclass(frozen=True)
class OCRDependencyStatus:
    found: bool
    path: str
    source: str


@dataclass(frozen=True)
class OCREngineStatus:
    available: bool
    engine_name: str
    renderer: str
    message: str
    dependencies: dict[str, OCRDependencyStatus] = field(default_factory=dict)


class OCRExecutionError(RuntimeError):
    pass


def ocr_result_stats(
    pages: list[dict[str, object]],
    index: dict[str, object] | None,
    warning: str = "",
) -> dict[str, object]:
    return {
        "pages_processed": len(pages),
        "pages_with_text": sum(1 for page in pages if str(page.get("text") or "").strip()),
        "chunks_created": len(index["chunks"]) if index and isinstance(index.get("chunks"), list) else 0,
        "embeddings_created": int(index["embedded"]) if index and "embedded" in index else 0,
        "embeddings_cached": int(index["cached"]) if index and "cached" in index else 0,
        "warning": warning,
    }


class OCREngine(Protocol):
    name: str

    def status(self) -> OCREngineStatus:
        raise NotImplementedError

    def ocr_pdf(self, stored_path: str, source_path: str) -> list[OCRPage]:
        raise NotImplementedError


class TesseractPdfEngine:
    name = "tesseract"

    def __init__(self):
        self.tesseract = ""
        self.pdftoppm = ""
        self.mutool = ""

    def status(self) -> OCREngineStatus:
        dependencies = self._detect_dependencies()
        if not dependencies["tesseract"].found:
            return OCREngineStatus(False, self.name, "", OCR_UNAVAILABLE_MESSAGE, dependencies)
        renderer = self._renderer_name()
        if not renderer:
            return OCREngineStatus(
                False,
                self.name,
                "",
                "Tesseract is installed, but no PDF renderer was found. Install Poppler pdftoppm or MuPDF mutool.",
                dependencies,
            )
        return OCREngineStatus(True, self.name, renderer, "OCR is available.", dependencies)

    def _detect_dependencies(self) -> dict[str, OCRDependencyStatus]:
        dependencies = {
            "tesseract": self._detect_executable(
                "tesseract",
                [
                    r"%ProgramFiles%\Tesseract-OCR\tesseract.exe",
                    r"%ProgramFiles(x86)%\Tesseract-OCR\tesseract.exe",
                    r"%LocalAppData%\Programs\Tesseract-OCR\tesseract.exe",
                    r"%LocalAppData%\Microsoft\WinGet\Packages\UB-Mannheim.TesseractOCR_*\tesseract.exe",
                    r"%LocalAppData%\Microsoft\WinGet\Packages\Tesseract-OCR.Tesseract_*\tesseract.exe",
                ],
            ),
            "pdftoppm": self._detect_executable(
                "pdftoppm",
                [
                    r"%ProgramFiles%\Poppler\bin\pdftoppm.exe",
                    r"%ProgramFiles%\Poppler\Library\bin\pdftoppm.exe",
                    r"%ProgramFiles%\poppler*\Library\bin\pdftoppm.exe",
                    r"%LocalAppData%\Microsoft\WinGet\Packages\oschwartz10612.Poppler_*\Library\bin\pdftoppm.exe",
                    r"%LocalAppData%\Microsoft\WinGet\Packages\oschwartz10612.Poppler_*\poppler*\Library\bin\pdftoppm.exe",
                ],
            ),
            "mutool": self._detect_executable(
                "mutool",
                [
                    r"%ProgramFiles%\MuPDF\mutool.exe",
                    r"%ProgramFiles%\mupdf*\mutool.exe",
                    r"%LocalAppData%\Microsoft\WinGet\Packages\ArtifexSoftware.mutool_*\mutool.exe",
                    r"%LocalAppData%\Microsoft\WinGet\Packages\ArtifexSoftware.mutool_*\**\mutool.exe",
                ],
            ),
        }
        self.tesseract = dependencies["tesseract"].path
        self.pdftoppm = dependencies["pdftoppm"].path
        self.mutool = dependencies["mutool"].path
        return dependencies

    def _detect_executable(self, name: str, windows_fallbacks: list[str]) -> OCRDependencyStatus:
        resolved = shutil.which(name)
        if resolved:
            return OCRDependencyStatus(True, resolved, "PATH")

        for pattern in windows_fallbacks:
            for candidate in glob(os.path.expandvars(pattern), recursive=True):
                path = Path(candidate)
                if path.is_file():
                    return OCRDependencyStatus(True, str(path), "windows_fallback")

        return OCRDependencyStatus(False, "", "")

    def ocr_pdf(self, stored_path: str, source_path: str) -> list[OCRPage]:
        status = self.status()
        if not status.available:
            raise OCRExecutionError(status.message)
        with tempfile.TemporaryDirectory(prefix="odysseus-ocr-") as tmp:
            images = self._render_pdf(Path(stored_path), Path(tmp), status.renderer)
            pages: list[OCRPage] = []
            for page_number, image in enumerate(images, start=1):
                text, confidence = self._run_tesseract(image)
                pages.append(
                    OCRPage(
                        source_path=source_path,
                        page_number=page_number,
                        engine_name=self.name,
                        confidence=confidence,
                        text=text,
                    )
                )
            return pages

    def _renderer_name(self) -> str:
        if self.pdftoppm:
            return "pdftoppm"
        if self.mutool:
            return "mutool"
        return ""

    def _render_pdf(self, pdf: Path, tmp: Path, renderer: str) -> list[Path]:
        if renderer == "pdftoppm":
            prefix = tmp / "page"
            self._run_ocr_subprocess(
                [self.pdftoppm or "pdftoppm", "-png", "-r", "200", str(pdf), str(prefix)],
                "pdftoppm",
            )
            images = sorted(tmp.glob("page-*.png"))
            if not images:
                raise OCRExecutionError("pdftoppm rendered no page images.")
            return images
        if renderer == "mutool":
            out_pattern = tmp / "page-%d.png"
            self._run_ocr_subprocess(
                [self.mutool or "mutool", "draw", "-r", "200", "-o", str(out_pattern), str(pdf)],
                "mutool",
            )
            images = sorted(tmp.glob("page-*.png"))
            if not images:
                raise OCRExecutionError("mutool rendered no page images.")
            return images
        raise OCRExecutionError("No supported PDF renderer available.")

    def _run_tesseract(self, image: Path) -> tuple[str, float | None]:
        proc = self._run_ocr_subprocess(
            [self.tesseract or "tesseract", str(image), "stdout", "tsv"],
            "Tesseract OCR",
        )
        stdout = self._safe_subprocess_text(proc.stdout)
        words: list[str] = []
        confidences: list[float] = []
        for line in stdout.splitlines()[1:]:
            parts = line.split("\t")
            if len(parts) < 12:
                continue
            text = parts[11].strip()
            if not text:
                continue
            words.append(text)
            try:
                confidence = float(parts[10])
            except ValueError:
                continue
            if confidence >= 0:
                confidences.append(confidence)
        avg_confidence = sum(confidences) / len(confidences) if confidences else None
        return " ".join(words), avg_confidence

    def _run_ocr_subprocess(self, command: list[str], label: str) -> subprocess.CompletedProcess[str]:
        try:
            proc = subprocess.run(
                command,
                check=False,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
        except FileNotFoundError as exc:
            raise OCRExecutionError(f"{label} executable was not found: {command[0]}") from exc
        except OSError as exc:
            raise OCRExecutionError(f"{label} failed to start: {exc}") from exc

        if proc.returncode != 0:
            detail = (
                self._safe_subprocess_text(proc.stderr).strip()
                or self._safe_subprocess_text(proc.stdout).strip()
                or f"exit code {proc.returncode}"
            )
            raise OCRExecutionError(f"{label} failed: {detail}")
        return proc

    def _safe_subprocess_text(self, value: object) -> str:
        if value is None:
            return ""
        if isinstance(value, bytes):
            return value.decode("utf-8", errors="replace")
        return str(value)


class OCRService:
    def __init__(
        self,
        documents: DocumentService,
        rag: RAGService,
        engine: OCREngine | None = None,
    ):
        self.documents = documents
        self.rag = rag
        self.engine = engine or TesseractPdfEngine()

    def status(self) -> dict[str, object]:
        status = self.engine.status()
        logger.info(
            "ocr status available=%s engine=%s renderer=%s message=%s",
            status.available,
            status.engine_name,
            status.renderer,
            status.message,
        )
        return {
            "available": status.available,
            "engine_name": status.engine_name,
            "renderer": status.renderer,
            "message": status.message,
            "dependencies": {
                name: {
                    "found": dependency.found,
                    "path": dependency.path,
                    "source": dependency.source,
                }
                for name, dependency in status.dependencies.items()
            },
        }

    def run_document_ocr(self, document_id: str) -> dict[str, object]:
        document = self.documents.get(document_id)
        if document["file_type"] != "pdf":
            raise ValueError("OCR is only available for PDF documents in Milestone 3.")

        status = self.engine.status()
        logger.info(
            "ocr requested document_id=%s file_name=%s available=%s renderer=%s",
            document_id,
            document["file_name"],
            status.available,
            status.renderer,
        )
        if not status.available:
            self.documents.mark_ocr_unavailable(document_id, status.message)
            pages: list[dict[str, object]] = []
            return {
                "document": self.documents.get(document_id),
                "ocr_status": self.status(),
                "ocr_pages": pages,
                "stats": ocr_result_stats(pages, None, status.message),
                "index": None,
            }

        self.documents.mark_ocr_running(document_id, status.engine_name)
        try:
            pages = self.engine.ocr_pdf(document["stored_path"], document["source_path"])
        except OCRExecutionError as exc:
            logger.warning("ocr execution failed document_id=%s error=%s", document_id, exc)
            return self._empty_result(document_id, str(exc))
        if not any(page.text.strip() for page in pages):
            message = "OCR ran, but no text was extracted."
            self.documents.replace_ocr_pages(document_id, pages, index_status="no_text")
            logger.warning("ocr no_text document_id=%s pages=%s", document_id, len(pages))
            return self._empty_result(document_id, message, self.documents.ocr_pages(document_id))

        self.documents.mark_ocr_ready(document_id, status.engine_name)
        self.documents.replace_pages_from_ocr(document_id, pages)
        indexed = self.rag.index_document(document_id)
        self.documents.mark_ocr_indexed(document_id, status.engine_name)
        self.documents.link_ocr_chunks(document_id)
        stored_pages = self.documents.ocr_pages(document_id)
        index = {
            **indexed,
            "document": self.documents.get(document_id),
        }
        logger.info(
            "ocr indexed document_id=%s pages=%s chunks=%s embedded=%s cached=%s",
            document_id,
            len(stored_pages),
            len(index["chunks"]),
            index["embedded"],
            index["cached"],
        )

        return {
            "document": self.documents.get(document_id),
            "ocr_status": self.status(),
            "ocr_pages": stored_pages,
            "stats": ocr_result_stats(stored_pages, index),
            "index": index,
        }

    def _empty_result(
        self,
        document_id: str,
        message: str,
        pages: list[dict[str, object]] | None = None,
    ) -> dict[str, object]:
        self.documents.mark_ocr_unavailable(document_id, message)
        stored_pages = pages or []
        return {
            "document": self.documents.get(document_id),
            "ocr_status": self.status(),
            "ocr_pages": stored_pages,
            "stats": ocr_result_stats(stored_pages, None, message),
            "index": None,
        }
