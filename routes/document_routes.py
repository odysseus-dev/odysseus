"""Document routes — CRUD for living documents with version history."""

import uuid
import logging
from datetime import datetime, timezone
from typing import Dict, Any, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request, UploadFile, File, Form

from sqlalchemy import func
from core.database import SessionLocal, Document, DocumentVersion
from core.database import Session as DbSession
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


def _aggregate_language_facets(lang_rows):
    """Sum document counts per display language for the library facet.

    NULL-language and explicit "text" rows share the "text" bucket (the
    language filter treats them as one), so they must be ADDED. The old dict
    comprehension keyed both to "text", silently overwriting one group and
    undercounting the facet versus what the filter actually returns.
    """
    out = {}
    for lang, cnt in lang_rows:
        key = lang or "text"
        out[key] = out.get(key, 0) + cnt
    return out



from routes.document_helpers import (
    DocumentCreate, DocumentUpdate, DocumentPatch,
    _doc_to_dict, _version_to_dict,
    _verify_doc_owner, _owner_session_filter,
    _slug, _resolve_user_upload_path, _assert_pdf_marker_upload_owned, _derive_title,
    _PDF_RENDER_SCALE,
)


def setup_document_routes(session_manager, upload_handler=None) -> APIRouter:
    router = APIRouter(tags=["documents"])

    def _locate_current_user_upload(request: Request, upload_id: str, user: Optional[str]):
        if upload_handler is None:
            return None
        auth_manager = getattr(getattr(request.app, "state", None), "auth_manager", None)
        return _resolve_user_upload_path(upload_handler, upload_id, user, auth_manager)

    def _load_pdf_viewer_fitz():
        from src.pdf_runtime import load_pymupdf_for_pdf_viewer

        try:
            return load_pymupdf_for_pdf_viewer()
        except RuntimeError as exc:
            raise HTTPException(503, str(exc)) from exc

    # ---- POST /api/document ----
    @router.post("/api/document")
    async def create_document(request: Request, req: DocumentCreate) -> Dict[str, Any]:
        from src.auth_helpers import require_privilege
        user = require_privilege(request, "can_use_documents")
        db = SessionLocal()
        try:
            # session_id is optional: a doc can be a session-less "library" doc
            # (e.g. files imported from the library) — session_id is nullable and
            # the doc is owner-stamped, so it lives in the library on its own.
            session = None
            if req.session_id:
                session = db.query(DbSession).filter(DbSession.id == req.session_id).first()
                if not session:
                    raise HTTPException(404, "Session not found")
                # Match the lenient ownership model the rest of the app uses
                # (see _owner_filter): only block when an AUTHENTICATED user is
                # writing into a DIFFERENT user's session. In single-user /
                # unconfigured / localhost-bypass mode the middleware leaves
                # current_user unset (None), and those sessions are already
                # served freely everywhere else.
                if user and session.owner and session.owner != user:
                    raise HTTPException(403, "Cannot create document in another user's session")

            doc_id = str(uuid.uuid4())
            ver_id = str(uuid.uuid4())

            # If no language was supplied (e.g. cloning a doc whose language
            # was never set), detect it from the content rather than storing
            # NULL — which made the editor fall back to plain text. Defaults
            # to markdown for prose.
            language = req.language
            if not language:
                from src.tool_implementations import _looks_like_email_document, _sniff_doc_language
                language = _sniff_doc_language(req.content)
            else:
                from src.tool_implementations import _looks_like_email_document
            if _looks_like_email_document(req.content, req.title):
                language = "email"

            _assert_pdf_marker_upload_owned(request, req.content, user, upload_handler)

            doc = Document(
                id=doc_id,
                session_id=req.session_id,
                title=req.title,
                language=language,
                current_content=req.content,
                version_count=1,
                is_active=True,
                # Stamp ownership directly so the doc survives its session
                # being deleted. Fall back to the session's owner when the
                # request is unauthenticated (single-user / localhost bypass).
                owner=user or (session.owner if session else None),
            )
            ver = DocumentVersion(
                id=ver_id,
                document_id=doc_id,
                version_number=1,
                content=req.content,
                summary="Initial version",
                source="user",
            )
            db.add(doc)
            db.add(ver)
            db.commit()
            db.refresh(doc)
            try:
                from src.event_bus import fire_event
                fire_event("document_created", doc.owner)
            except Exception:
                logger.debug("document_created event dispatch failed", exc_info=True)
            return _doc_to_dict(doc)
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"Failed to create document: {e}")
            raise HTTPException(500, f"Failed to create document: {e}")
        finally:
            db.close()

    # ---- POST /api/documents/import-pdf ----
    @router.post("/api/documents/import-pdf")
    async def import_pdf(
        request: Request,
        file: UploadFile = File(...),
        session_id: Optional[str] = Form(None),
    ) -> Dict[str, Any]:
        """Upload a PDF and create the matching Document.

        Detects AcroForm fields — if any, creates a form-backed markdown doc
        (clickable inputs in the PDF view). Otherwise creates a plain PDF doc
        with a `pdf_source` marker so the viewer renders the pages without
        overlays.
        """
        from src.pdf_forms import has_form_fields, extract_fields
        from src.pdf_form_doc import (
            save_field_sidecar,
            create_form_markdown_document,
            create_plain_pdf_document,
        )
        from src.document_processor import _process_pdf, strip_pdf_content_marker
        import os

        from src.auth_helpers import require_privilege
        user = require_privilege(request, "can_use_documents")

        # session_id is optional — a library import isn't tied to a chat. When
        # given, validate it; otherwise the PDF becomes a session-less library
        # doc (the doc creators below already handle a missing session).
        if session_id:
            db = SessionLocal()
            try:
                sess = db.query(DbSession).filter(DbSession.id == session_id).first()
                if not sess:
                    raise HTTPException(404, "Session not found")
                if user and sess.owner and sess.owner != user:
                    raise HTTPException(403, "Cannot import into another user's session")
            finally:
                db.close()

        if upload_handler is None:
            raise HTTPException(500, "Upload handler not configured")

        client_ip = request.client.host if request.client else "unknown"
        try:
            meta = upload_handler.save_upload(file, client_ip, owner=user)
        except HTTPException:
            raise
        except Exception as e:
            logger.error(f"PDF import save_upload failed: {e}")
            raise HTTPException(500, f"Upload failed: {e}")

        upload_id = meta["id"]
        pdf_path = _locate_current_user_upload(request, upload_id, user)
        if not pdf_path:
            raise HTTPException(500, "Saved PDF could not be located")

        title = os.path.splitext(meta.get("original_name") or meta.get("name") or upload_id)[0]
        try:
            body_text = strip_pdf_content_marker(_process_pdf(pdf_path))
        except Exception:
            body_text = None

        is_form = False
        try:
            is_form = has_form_fields(pdf_path)
        except Exception as e:
            logger.warning(f"has_form_fields failed for {pdf_path}: {e}")

        if is_form:
            fields = extract_fields(pdf_path)
            save_field_sidecar(pdf_path, fields)
            doc_id = create_form_markdown_document(
                session_id=session_id,
                fields=fields,
                upload_id=upload_id,
                title=title,
                intro_text=body_text,
            )
        else:
            doc_id = create_plain_pdf_document(
                session_id=session_id,
                upload_id=upload_id,
                title=title,
                body_text=body_text,
            )

        if not doc_id:
            raise HTTPException(500, "Failed to create document for PDF")

        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(500, "Created document not found")
            # The PDF doc creators stamp owner from the session only; a
            # session-less library import leaves owner NULL, which the Library's
            # owner filter then hides. Stamp the requesting user so it shows.
            if not doc.owner and user:
                doc.owner = user
                db.commit()
                db.refresh(doc)
            return _doc_to_dict(doc)
        finally:
            db.close()

    # ---- GET /api/documents/library ----
    @router.get("/api/documents/library")
    async def documents_library(
        request: Request,
        search: Optional[str] = Query(None),
        language: Optional[str] = Query(None),
        sort: str = Query("recent"),
        offset: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=50),
        archived: bool = Query(False),
    ) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            from sqlalchemy import or_
            # Archived view shows ONLY archived docs; the default view excludes
            # them (NULL = legacy rows that predate the column = not archived).
            _arch_cond = (Document.archived == True) if archived else or_(
                Document.archived == False, Document.archived.is_(None))
            # Language facet counts (owner-filtered)
            lang_q = (
                db.query(Document.language, func.count(Document.id))
                .outerjoin(DbSession, Document.session_id == DbSession.id)
                .filter(Document.is_active == True).filter(_arch_cond)
            )
            lang_q = _owner_session_filter(lang_q, user)
            lang_rows = lang_q.group_by(Document.language).all()
            languages = _aggregate_language_facets(lang_rows)

            # Session count (owner-filtered)
            sc_q = (
                db.query(func.count(func.distinct(Document.session_id)))
                .outerjoin(DbSession, Document.session_id == DbSession.id)
                .filter(Document.is_active == True).filter(_arch_cond)
            )
            sc_q = _owner_session_filter(sc_q, user)
            session_count = sc_q.scalar()

            # Base query
            q = (
                db.query(Document, DbSession.name)
                .outerjoin(DbSession, Document.session_id == DbSession.id)
                .filter(Document.is_active == True).filter(_arch_cond)
            )
            q = _owner_session_filter(q, user)

            # Search filter — split on whitespace and require EACH term to
            # match (title OR content). A single `%foo bar%` LIKE only matched
            # the exact adjacent phrase, so any multi-word query with a space
            # silently returned nothing. Per-term AND makes "machine learning"
            # match docs containing both words regardless of position/order.
            if search:
                for tok in search.split():
                    term = f"%{tok}%"
                    q = q.filter(
                        Document.title.ilike(term) | Document.current_content.ilike(term)
                    )

            # Language filter
            if language:
                if language == "text":
                    q = q.filter((Document.language == None) | (Document.language == "text"))
                else:
                    q = q.filter(Document.language == language)

            # Total before pagination
            total = q.count()

            # Sorting
            if sort == "oldest":
                q = q.order_by(Document.created_at.asc())
            elif sort == "edits":
                q = q.order_by(Document.version_count.desc())
            elif sort == "alpha":
                q = q.order_by(Document.title.asc())
            else:  # recent
                q = q.order_by(Document.updated_at.desc())

            rows = q.offset(offset).limit(limit).all()

            documents = []
            for doc, session_name in rows:
                documents.append({
                    "id": doc.id,
                    "session_id": doc.session_id,
                    "session_name": session_name,
                    "title": doc.title,
                    "language": doc.language or "text",
                    "preview": (doc.current_content or "")[:500],
                    "version_count": doc.version_count,
                    "created_at": (doc.created_at.isoformat() + "Z") if doc.created_at else None,
                    "updated_at": (doc.updated_at.isoformat() + "Z") if doc.updated_at else None,
                })

            return {
                "documents": documents,
                "total": total,
                "languages": languages,
                "session_count": session_count,
            }
        except Exception as e:
            logger.error(f"Failed to fetch document library: {e}")
            raise HTTPException(500, f"Failed to fetch document library: {e}")
        finally:
            db.close()

    # ---- GET /api/documents/{session_id} ----
    @router.get("/api/documents/{session_id}")
    async def list_documents(request: Request, session_id: str) -> List[Dict[str, Any]]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            if not user:
                raise HTTPException(403, "Authentication required")
            session = db.query(DbSession).filter(DbSession.id == session_id).first()
            # v2 review HIGH-9: raise 403 explicitly when the caller
            # can't see this session, instead of returning [] which the
            # UI treats identically to "no docs" and silently masks
            # auth failures.
            if not session:
                raise HTTPException(404, "Session not found")
            if user and session.owner and session.owner != user:
                raise HTTPException(403, "Access denied")
            docs = db.query(Document).filter(
                Document.session_id == session_id
            ).order_by(Document.created_at.desc()).all()
            return [_doc_to_dict(d) for d in docs]
        finally:
            db.close()

    # ---- GET /api/document/{doc_id} ----
    @router.get("/api/document/{doc_id}")
    async def get_document(request: Request, doc_id: str) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)
            return _doc_to_dict(doc)
        finally:
            db.close()

    # ---- POST /api/document/{doc_id}/archive — soft-archive / restore ----
    @router.post("/api/document/{doc_id}/archive")
    async def archive_document(request: Request, doc_id: str, archived: bool = Query(True)) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)
            doc.archived = bool(archived)
            db.commit()
            return {"ok": True, "id": doc_id, "archived": doc.archived}
        finally:
            db.close()

    # ---- POST /api/document/{doc_id}/extract-pdf-text ----
    @router.post("/api/document/{doc_id}/extract-pdf-text")
    async def extract_pdf_text(request: Request, doc_id: str) -> Dict[str, Any]:
        """Re-run pypdf+VL text extraction against the PDF linked to this doc
        and merge the result into the doc's markdown content. Idempotent — the
        existing body (everything below the title heading) is replaced.

        Lets the AI see PDF contents for old docs that were imported before
        text extraction was wired, plus for scanned/image-only PDFs where the
        VL model picks up text the basic pypdf path missed."""
        import re
        from src.document_processor import _process_pdf, strip_pdf_content_marker
        from src.pdf_form_doc import find_source_upload_id

        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)

            content = doc.current_content or ""
            upload_id = find_source_upload_id(content)
            if not upload_id:
                raise HTTPException(400, "Document is not a PDF — no pdf_source marker found")

            pdf_path = _locate_current_user_upload(request, upload_id, user)
            if not pdf_path:
                raise HTTPException(404, "Source PDF could not be located")

            try:
                body_text = strip_pdf_content_marker(_process_pdf(pdf_path))
            except Exception as e:
                logger.error(f"extract_pdf_text failed for {pdf_path}: {e}")
                raise HTTPException(500, f"Extraction failed: {e}")

            if not body_text:
                return {"ok": True, "id": doc_id, "extracted": False, "reason": "No readable content"}

            # Preserve everything up through the title (front-matter marker +
            # first H1) and replace the rest with the freshly extracted text.
            head_re = re.compile(r'^(<!--[^>]+-->\s*\n+#[^\n]*\n+)', re.MULTILINE)
            head_match = head_re.match(content)
            head = head_match.group(1) if head_match else (content.splitlines()[0] + "\n\n# " + (doc.title or "PDF") + "\n\n")
            doc.current_content = head + body_text.strip() + "\n"
            doc.version_count = (doc.version_count or 1) + 1
            db.add(DocumentVersion(
                id=str(__import__("uuid").uuid4()),
                document_id=doc_id,
                version_number=doc.version_count,
                content=doc.current_content,
                summary="PDF text re-extracted (OCR)",
                source="ocr",
            ))
            db.commit()
            return {"ok": True, "id": doc_id, "extracted": True, "chars": len(body_text)}
        finally:
            db.close()

    # ---- POST /api/documents/export-zip — bundle selected docs into a .zip ----
    @router.post("/api/documents/export-zip")
    async def documents_export_zip(request: Request):
        """Zip the selected documents (each as a text file with the right
        extension) — mirrors the gallery's bulk download-zip so multi-export
        is one file instead of a blocked flood of individual downloads."""
        user = get_current_user(request)
        try:
            data = await request.json()
        except Exception:
            data = {}
        ids = data.get("ids") or []
        if not ids:
            raise HTTPException(400, "No documents specified")
        _ext = {
            "javascript": ".js", "python": ".py", "html": ".html", "css": ".css",
            "markdown": ".md", "json": ".json", "yaml": ".yml", "bash": ".sh",
            "sql": ".sql", "rust": ".rs", "go": ".go", "java": ".java", "c": ".c",
            "cpp": ".cpp", "typescript": ".ts", "ruby": ".rb", "php": ".php",
            "text": ".txt", "xml": ".xml", "toml": ".toml", "ini": ".ini",
        }
        db = SessionLocal()
        try:
            import io
            import re
            import zipfile
            from fastapi import Response
            docs = db.query(Document).filter(Document.id.in_(ids)).all()
            buf = io.BytesIO()
            used = set()
            wrote = 0
            with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
                for doc in docs:
                    try:
                        _verify_doc_owner(db, doc, user)
                    except HTTPException:
                        continue   # skip docs the user doesn't own
                    ext = _ext.get(doc.language or "text", ".txt")
                    base = (doc.title or "document").strip() or "document"
                    base = re.sub(r"[^\w\-. ]+", "", base)[:60].strip() or doc.id
                    name = base if "." in base else base + ext
                    i = 1
                    while name in used:
                        name = f"{base}-{i}" + ("" if "." in base else ext)
                        i += 1
                    used.add(name)
                    zf.writestr(name, doc.current_content or "")
                    wrote += 1
            if not wrote:
                raise HTTPException(404, "No documents found")
            return Response(
                content=buf.getvalue(),
                media_type="application/zip",
                headers={"Content-Disposition": 'attachment; filename="documents.zip"'},
            )
        finally:
            db.close()

    # ---- PUT /api/document/{doc_id} — user manual edit ----
    # Coalesce window: if the last user version was saved within this many
    # seconds, update it in-place (user is still actively editing).
    # Once the gap exceeds this, the next save creates a new version.
    VERSION_COALESCE_SECONDS = 60

    @router.put("/api/document/{doc_id}")
    async def update_document(request: Request, doc_id: str, req: DocumentUpdate) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)

            # Skip if content is identical
            if doc.current_content == req.content:
                return _doc_to_dict(doc)

            _assert_pdf_marker_upload_owned(request, req.content, user, upload_handler)

            # Check if we can coalesce with the latest version
            latest_ver = db.query(DocumentVersion).filter(
                DocumentVersion.document_id == doc_id,
            ).order_by(DocumentVersion.version_number.desc()).first()

            now = datetime.now(timezone.utc)
            coalesced = False
            if latest_ver and latest_ver.source == "user":
                ver_time = latest_ver.created_at
                if ver_time.tzinfo is None:
                    ver_time = ver_time.replace(tzinfo=timezone.utc)
                age = (now - ver_time).total_seconds()
                if age < VERSION_COALESCE_SECONDS:
                    # Update the existing version in-place
                    latest_ver.content = req.content
                    latest_ver.created_at = now
                    if req.summary:
                        latest_ver.summary = req.summary
                    coalesced = True

            if not coalesced:
                new_ver = doc.version_count + 1
                ver = DocumentVersion(
                    id=str(uuid.uuid4()),
                    document_id=doc_id,
                    version_number=new_ver,
                    content=req.content,
                    summary=req.summary or "Manual edit",
                    source="user",
                )
                doc.version_count = new_ver
                db.add(ver)

            doc.current_content = req.content
            db.commit()
            db.refresh(doc)
            return _doc_to_dict(doc)
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(500, f"Failed to update document: {e}")
        finally:
            db.close()

    # ---- PATCH /api/document/{doc_id} — metadata only ----
    @router.patch("/api/document/{doc_id}")
    async def patch_document(request: Request, doc_id: str, req: DocumentPatch) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)
            if req.title is not None:
                doc.title = req.title
            if req.language is not None:
                doc.language = req.language
            if req.session_id is not None:
                # Empty string = unlink from session
                doc.session_id = req.session_id if req.session_id else None
                if not req.session_id:
                    # Tab closed / doc detached from its session — drop the
                    # in-memory active-doc pointer so the last-resort injection
                    # path doesn't re-surface this doc in a later chat (#1160).
                    try:
                        from src.tool_implementations import clear_active_document
                        clear_active_document(doc_id)
                    except Exception:
                        pass
            db.commit()
            db.refresh(doc)
            return _doc_to_dict(doc)
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(500, str(e))
        finally:
            db.close()

    # ---- DELETE /api/document/{doc_id} — soft delete ----
    @router.delete("/api/document/{doc_id}")
    async def delete_document(request: Request, doc_id: str) -> Dict[str, str]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)
            doc.is_active = False
            # Closed/deleted — drop the in-memory active-doc pointer so it isn't
            # re-injected into a later, unrelated chat (#1160).
            try:
                from src.tool_implementations import clear_active_document
                clear_active_document(doc_id)
            except Exception:
                pass
            db.commit()
            return {"status": "deleted", "id": doc_id}
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(500, str(e))
        finally:
            db.close()

    # ---- GET /api/document/{doc_id}/versions ----
    @router.get("/api/document/{doc_id}/versions")
    async def list_versions(request: Request, doc_id: str) -> List[Dict[str, Any]]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            # Verify ownership before listing versions
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if doc:
                _verify_doc_owner(db, doc, user)
            versions = db.query(DocumentVersion).filter(
                DocumentVersion.document_id == doc_id
            ).order_by(DocumentVersion.version_number.desc()).all()
            return [{
                "id": v.id,
                "version_number": v.version_number,
                "content": v.content,
                "summary": v.summary,
                "source": v.source,
                "created_at": v.created_at.isoformat() if v.created_at else None,
            } for v in versions]
        finally:
            db.close()

    # ---- GET /api/document/{doc_id}/version/{num} ----
    @router.get("/api/document/{doc_id}/version/{num}")
    async def get_version(request: Request, doc_id: str, num: int) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            # Verify ownership
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if doc:
                _verify_doc_owner(db, doc, user)
            ver = db.query(DocumentVersion).filter(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.version_number == num,
            ).first()
            if not ver:
                raise HTTPException(404, "Version not found")
            return _version_to_dict(ver)
        finally:
            db.close()

    # ---- POST /api/document/{doc_id}/restore/{num} ----
    @router.post("/api/document/{doc_id}/restore/{num}")
    async def restore_version(request: Request, doc_id: str, num: int) -> Dict[str, Any]:
        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)

            old_ver = db.query(DocumentVersion).filter(
                DocumentVersion.document_id == doc_id,
                DocumentVersion.version_number == num,
            ).first()
            if not old_ver:
                raise HTTPException(404, "Version not found")

            new_ver_num = doc.version_count + 1
            ver = DocumentVersion(
                id=str(uuid.uuid4()),
                document_id=doc_id,
                version_number=new_ver_num,
                content=old_ver.content,
                summary=f"Restored from v{num}",
                source="user",
            )
            doc.current_content = old_ver.content
            doc.version_count = new_ver_num
            db.add(ver)
            db.commit()
            db.refresh(doc)
            return _doc_to_dict(doc)
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            raise HTTPException(500, str(e))
        finally:
            db.close()

    # ---- POST /api/documents/tidy — clean up broken/empty documents ----
    @router.post("/api/documents/tidy")
    async def tidy_documents(request: Request) -> Dict[str, Any]:
        """Fix empty titles and remove broken/empty documents (user's docs only)."""
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = (
                db.query(Document)
                .outerjoin(DbSession, Document.session_id == DbSession.id)
                .filter(Document.is_active == True)
                .filter((Document.archived == False) | (Document.archived.is_(None)))
            )
            q = _owner_session_filter(q, user)
            docs = q.all()
            fixed_titles = 0
            deleted = 0

            # Same junk-detection logic as the scheduled tidy_documents
            # action (src/document_actions.py). Keep these two in sync.
            import re as _re
            from src.document_actions import _JUNK_TITLES

            to_delete = []
            for doc in docs:
                content = (doc.current_content or "").strip()
                title_raw = (doc.title or "").strip()
                title = title_raw.lower()

                # Strip markdown noise to get a "real" character count
                stripped = _re.sub(r"^#{1,6}\s+", "", content, flags=_re.MULTILINE)
                stripped = _re.sub(r"[*_`>\-=]+", "", stripped)
                stripped = _re.sub(r"\s+", " ", stripped).strip()
                real_len = len(stripped)

                # Detect email-scaffold stubs: "To: \nSubject: \n---\n" style
                # bodies with nothing typed in. Stub = every meaningful line
                # is a header label (To:/From:/Subject:/...) with no real
                # value (blank, "empty", "(empty)", "-", "none", "n/a").
                _is_email_stub = False
                _HEADER_RE = _re.compile(r"^(to|from|cc|bcc|subject|reply-to):\s*(.*)$", _re.I)
                _PLACEHOLDER_VALS = {"", "empty", "(empty)", "-", "—", "none", "n/a", "na", "tbd"}
                if title in ("new email", "new mail", "new message") or doc.language == "email":
                    body_lines = [ln.strip() for ln in content.split("\n")
                                  if ln.strip() and ln.strip() != "---"]
                    def _is_filler(ln):
                        m = _HEADER_RE.match(ln)
                        if not m:
                            return False
                        val = (m.group(2) or "").strip().lower()
                        return val in _PLACEHOLDER_VALS
                    has_real_body = any(not _is_filler(ln) for ln in body_lines)
                    if body_lines and not has_real_body:
                        _is_email_stub = True

                # Hard-delete obviously empty / junk documents
                if not content or content in ("", "# Untitled"):
                    to_delete.append(doc); deleted += 1; continue
                if _is_email_stub:
                    to_delete.append(doc); deleted += 1; continue
                if title in _JUNK_TITLES:
                    to_delete.append(doc); deleted += 1; continue
                if real_len < 30:
                    to_delete.append(doc); deleted += 1; continue
                if "\n" not in content and real_len < 50:
                    to_delete.append(doc); deleted += 1; continue

                # Fix empty or placeholder titles on survivors
                if not title_raw or title_raw == "Untitled":
                    new_title = _derive_title(content)
                    if new_title and new_title != "Untitled":
                        doc.title = new_title
                        fixed_titles += 1

            for doc in to_delete:
                db.delete(doc)

            # Also clean up inactive empty docs from previous soft-deletes
            inactive_q = (
                db.query(Document)
                .outerjoin(DbSession, Document.session_id == DbSession.id)
                .filter(Document.is_active == False)
                .filter((Document.current_content == None) | (Document.current_content == ""))
            )
            inactive_q = _owner_session_filter(inactive_q, user)
            inactive_docs = inactive_q.all()
            for doc in inactive_docs:
                db.delete(doc)
            deleted += len(inactive_docs)

            db.commit()
            return {
                "fixed_titles": fixed_titles,
                "deleted": deleted,
                "message": f"Fixed {fixed_titles} title{'s' if fixed_titles != 1 else ''}, removed {deleted} empty document{'s' if deleted != 1 else ''}",
            }
        except Exception as e:
            db.rollback()
            logger.error(f"Document tidy failed: {e}")
            raise HTTPException(500, f"Tidy failed: {e}")
        finally:
            db.close()

    # ---- POST /api/documents/ai-tidy — AI-powered cleanup of junk/test documents ----
    @router.post("/api/documents/ai-tidy")
    async def ai_tidy_documents(request: Request) -> Dict[str, Any]:
        """Use AI to judge if documents are junk/test/accidental, then delete them.
        Caches verdicts so previously-reviewed docs are skipped."""
        from src.task_endpoint import resolve_task_endpoint
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async

        user = get_current_user(request)
        url, model, headers = resolve_task_endpoint()
        if not url or not model:
            # Fall back to default endpoint
            url, model, headers = resolve_endpoint("default")
        if not url or not model:
            raise HTTPException(500, "No endpoint configured for AI tidy")

        db = SessionLocal()
        try:
            q = (
                db.query(Document)
                .outerjoin(DbSession, Document.session_id == DbSession.id)
                .filter(Document.is_active == True)
                .filter((Document.archived == False) | (Document.archived.is_(None)))
            )
            q = _owner_session_filter(q, user)
            docs = q.all()

            # Only review docs that haven't been reviewed yet
            to_review = [d for d in docs if not d.tidy_verdict]
            if not to_review:
                return {"deleted": 0, "reviewed": 0, "message": "All documents already reviewed"}

            # Build a batch prompt — review up to 30 at a time
            batch = to_review[:30]
            doc_list = []
            for i, doc in enumerate(batch):
                preview = (doc.current_content or "")[:300].strip()
                doc_list.append(f"[{i}] title=\"{doc.title}\" lang={doc.language or 'text'} content_preview=\"{preview}\"")

            prompt = (
                "You are a document library cleaner. For each document below, decide if it is JUNK "
                "(test, accidental, placeholder, empty-ish, tool-test, throwaway) or KEEP (real content worth saving).\n\n"
                "Respond with ONLY a JSON array of verdicts, one per document, like: [\"junk\",\"keep\",\"junk\",...]\n"
                "No explanation, no markdown, just the JSON array.\n\n"
                + "\n".join(doc_list)
            )

            response = await llm_call_async(
                url, model,
                [{"role": "system", "content": "You classify documents as junk or keep. Respond only with a JSON array."},
                 {"role": "user", "content": prompt}],
                temperature=0.1,
                max_tokens=200,
                headers=headers,
                timeout=30,
            )

            # Parse verdicts
            import re
            match = re.search(r'\[.*?\]', response, re.DOTALL)
            if not match:
                raise HTTPException(500, "AI returned invalid response")

            import json as _json
            verdicts = _json.loads(match.group())

            deleted = 0
            reviewed = 0
            for i, doc in enumerate(batch):
                if i >= len(verdicts):
                    break
                verdict = str(verdicts[i] or "").lower().strip()
                if verdict == "junk":
                    doc.tidy_verdict = "junk"
                    db.delete(doc)
                    deleted += 1
                else:
                    doc.tidy_verdict = "keep"
                reviewed += 1

            db.commit()
            return {
                "deleted": deleted,
                "reviewed": reviewed,
                "remaining": len(to_review) - len(batch),
                "message": f"Reviewed {reviewed}, removed {deleted} junk document{'s' if deleted != 1 else ''}",
            }
        except HTTPException:
            raise
        except Exception as e:
            db.rollback()
            logger.error(f"AI tidy failed: {e}")
            raise HTTPException(500, f"AI tidy failed: {e}")
        finally:
            db.close()

    # ---- POST /api/document/{doc_id}/export-pdf/preview ----
    @router.post("/api/document/{doc_id}/export-pdf/preview")
    async def export_pdf_preview(doc_id: str, request: Request) -> Dict[str, Any]:
        """Return the field-value mapping that would be written to the PDF.

        Frontend shows this in a confirmation modal so the user can spot/fix
        any wrong values before triggering the actual download.
        """
        from src.pdf_form_doc import find_source_upload_id, parse_markdown_to_values, load_field_sidecar

        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)

            upload_id = find_source_upload_id(doc.current_content or "")
            if not upload_id:
                raise HTTPException(400, "Document is not linked to a source PDF")

            pdf_path = _locate_current_user_upload(request, upload_id, user)
            if not pdf_path:
                raise HTTPException(404, f"Source PDF {upload_id} not found in uploads")

            fields = load_field_sidecar(pdf_path)
            if not fields:
                raise HTTPException(404, "Field schema sidecar missing for source PDF")

            values = parse_markdown_to_values(doc.current_content or "")
            field_meta = {f["name"]: f for f in fields}

            preview = []
            for name, current in values.items():
                meta = field_meta.get(name)
                if not meta:
                    continue
                preview.append({
                    "name": name,
                    "label": meta.get("label") or name,
                    "type": meta.get("type"),
                    "options": meta.get("options") or [],
                    "page": meta.get("page"),
                    "value": current,
                })

            unknown = [
                name for name in values
                if name not in field_meta
            ]
            return {
                "doc_id": doc_id,
                "upload_id": upload_id,
                "fields": preview,
                "unknown_fields": unknown,
                "total": len(fields),
                "filled": sum(1 for p in preview if p["value"] not in ("", False, None)),
            }
        finally:
            db.close()

    # ---- GET /api/document/{doc_id}/render-pages ----
    @router.get("/api/document/{doc_id}/render-pages")
    async def render_pages(doc_id: str, request: Request) -> Dict[str, Any]:
        """Return per-page metadata for the interactive PDF view.

        Each page entry has its rendered-image dimensions (matching what
        /page/{n}.png returns at the same DPI) plus the list of form fields
        on that page with their rects translated to image-pixel coordinates.
        Frontend overlays HTML form controls at those positions.
        """
        from src.pdf_form_doc import find_source_upload_id, parse_markdown_to_values, load_field_sidecar

        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)
            upload_id = find_source_upload_id(doc.current_content or "")
            if not upload_id:
                raise HTTPException(400, "Document is not linked to a source PDF")
            pdf_path = _locate_current_user_upload(request, upload_id, user)
            if not pdf_path:
                raise HTTPException(404, f"Source PDF {upload_id} not found")

            fitz = _load_pdf_viewer_fitz()
            schema = load_field_sidecar(pdf_path) or []
            values = parse_markdown_to_values(doc.current_content or "")

            # Group fields by page
            by_page: Dict[int, list] = {}
            for f in schema:
                by_page.setdefault(f["page"], []).append(f)

            scale = _PDF_RENDER_SCALE
            pdf_doc = fitz.open(pdf_path)
            try:
                pages_out = []
                for page_index in range(pdf_doc.page_count):
                    page = pdf_doc[page_index]
                    page_no = page_index + 1
                    pw, ph = page.rect.width, page.rect.height
                    img_w = int(pw * scale)
                    img_h = int(ph * scale)
                    fields_out = []
                    for f in by_page.get(page_no, []):
                        x0, y0, x1, y1 = f["rect"]
                        fields_out.append({
                            "name": f["name"],
                            "type": f["type"],
                            "label": f.get("label") or "",
                            "options": f.get("options") or [],
                            "value": values.get(f["name"], f.get("value", "")),
                            "rect_px": [
                                int(x0 * scale), int(y0 * scale),
                                int(x1 * scale), int(y1 * scale),
                            ],
                        })
                    pages_out.append({
                        "page": page_no,
                        "width": img_w,
                        "height": img_h,
                        "fields": fields_out,
                    })
                return {"doc_id": doc_id, "scale": scale, "pages": pages_out}
            finally:
                pdf_doc.close()
        finally:
            db.close()

    # ---- GET /api/document/{doc_id}/page/{n}.png ----
    @router.get("/api/document/{doc_id}/page/{page_no}.png")
    async def render_page_png(doc_id: str, page_no: int, request: Request):
        """Render one page of the source PDF as a PNG (no values stamped — the
        frontend overlays HTML form inputs on top)."""
        from fastapi.responses import Response
        from src.pdf_form_doc import find_source_upload_id

        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)
            upload_id = find_source_upload_id(doc.current_content or "")
            if not upload_id:
                raise HTTPException(400, "Document is not linked to a source PDF")
            pdf_path = _locate_current_user_upload(request, upload_id, user)
            if not pdf_path:
                raise HTTPException(404, "Source PDF not found")
        finally:
            db.close()

        fitz = _load_pdf_viewer_fitz()
        pdf_doc = fitz.open(pdf_path)
        try:
            if page_no < 1 or page_no > pdf_doc.page_count:
                raise HTTPException(404, "Page out of range")
            page = pdf_doc[page_no - 1]
            mat = fitz.Matrix(_PDF_RENDER_SCALE, _PDF_RENDER_SCALE)
            pix = page.get_pixmap(matrix=mat, alpha=False)
            png_bytes = pix.tobytes("png")
            return Response(
                content=png_bytes,
                media_type="image/png",
                headers={"Cache-Control": "public, max-age=3600"},
            )
        finally:
            pdf_doc.close()

    # ---- POST /api/document/{doc_id}/ai-fill-annotations ----
    @router.post("/api/document/{doc_id}/ai-fill-annotations")
    async def ai_fill_annotations(doc_id: str, request: Request) -> Dict[str, Any]:
        """Ask a vision-capable LLM to locate fillable areas on a flat PDF and
        propose annotation values for each, given a free-form user instruction.

        Returns a list of annotations: [{page, x, y, w, h, value}] where x/y/w/h
        are page-percentages (0–100) — same coordinate system as the freeform
        annotations the frontend already renders.
        """
        import base64
        import json
        import fitz
        from src.pdf_form_doc import find_source_upload_id
        from src.document_processor import _resolve_vl_model, _load_vl_settings
        from src.llm_core import llm_call_async

        body = await request.json() if request.headers.get("content-type", "").startswith("application/json") else {}
        instruction = (body or {}).get("instruction", "").strip()
        if not instruction:
            raise HTTPException(400, "instruction is required")

        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)
            upload_id = find_source_upload_id(doc.current_content or "")
            if not upload_id:
                raise HTTPException(400, "Document is not linked to a source PDF")
            pdf_path = _locate_current_user_upload(request, upload_id, user)
            if not pdf_path:
                raise HTTPException(404, "Source PDF not found")
        finally:
            db.close()

        # Resolve VL model (admin-configured or auto-detected vision-capable)
        settings = _load_vl_settings()
        vl_model = settings.get("vision_model", "")
        try:
            url, model_id, headers = _resolve_vl_model(vl_model)
        except Exception as e:
            raise HTTPException(503, f"No vision model available: {e}")

        system_prompt = (
            "You analyze rendered PDF page images and propose values to fill in. "
            "For each blank line, box, underscore, or labeled space on the page that "
            "should be filled given the user's instruction, output one annotation. "
            "Coordinates are percentages (0-100) of the page width/height with the "
            "origin at top-left. Width/height should match the visible blank box. "
            "Return ONLY a JSON array, no prose, no markdown fences. Each entry: "
            '{"x": number, "y": number, "w": number, "h": number, "value": string}. '
            "If a region should not be filled, omit it. If nothing should be filled, "
            "return []."
        )

        all_annotations = []
        pdf_doc = fitz.open(pdf_path)
        try:
            for page_index in range(pdf_doc.page_count):
                page = pdf_doc[page_index]
                mat = fitz.Matrix(_PDF_RENDER_SCALE, _PDF_RENDER_SCALE)
                pix = page.get_pixmap(matrix=mat, alpha=False)
                png_bytes = pix.tobytes("png")
                b64 = base64.b64encode(png_bytes).decode("ascii")

                messages = [
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    f"User instruction:\n{instruction}\n\n"
                                    f"This is page {page_index + 1} of {pdf_doc.page_count}. "
                                    "Return JSON array of annotations to add to this page."
                                ),
                            },
                            {
                                "type": "image_url",
                                "image_url": {"url": f"data:image/png;base64,{b64}"},
                            },
                        ],
                    },
                ]
                try:
                    raw = await llm_call_async(
                        url, model_id, messages,
                        temperature=0.1, max_tokens=2000, headers=headers,
                    )
                except Exception as e:
                    logger.error(f"VL call failed on page {page_index + 1}: {e}")
                    continue

                raw = (raw or "").strip()
                if raw.startswith("```"):
                    raw = raw.split("\n", 1)[-1].rsplit("```", 1)[0].strip()
                try:
                    parsed = json.loads(raw)
                except Exception:
                    logger.warning(f"AI fill: page {page_index + 1} returned non-JSON: {raw[:200]}")
                    continue
                if not isinstance(parsed, list):
                    continue
                for item in parsed:
                    if not isinstance(item, dict):
                        continue
                    try:
                        x = float(item.get("x", 0))
                        y = float(item.get("y", 0))
                        w = float(item.get("w", 0))
                        h = float(item.get("h", 0))
                        value = str(item.get("value", "") or "")
                    except Exception:
                        continue
                    # Clamp + reject zero-size entries
                    if w <= 0.5 or h <= 0.3:
                        continue
                    x = max(0.0, min(99.0, x))
                    y = max(0.0, min(99.0, y))
                    w = max(0.5, min(100.0 - x, w))
                    h = max(0.3, min(100.0 - y, h))
                    if not value.strip():
                        continue
                    all_annotations.append({
                        "page": page_index + 1,
                        "x": round(x, 2),
                        "y": round(y, 2),
                        "w": round(w, 2),
                        "h": round(h, 2),
                        "value": value,
                    })
        finally:
            pdf_doc.close()

        return {"annotations": all_annotations}

    # ---- GET /api/document/{doc_id}/render-pdf ----
    @router.get("/api/document/{doc_id}/render-pdf")
    async def render_pdf(doc_id: str, request: Request):
        """Inline PDF preview filled with the current markdown values.

        Same plumbing as the export route, but no signature stamping and
        served inline (Content-Disposition: inline) so the browser can
        embed it in an iframe. Cache-busted by the caller via query string.
        """
        import base64
        import os
        import tempfile
        from fastapi.responses import FileResponse
        from starlette.background import BackgroundTask
        from src.pdf_form_doc import find_source_upload_id, parse_markdown_to_values, parse_markdown_annotations
        from src.pdf_forms import fill_fields, stamp_annotations
        from core.database import Signature

        # Track temp files for this request so they get unlinked AFTER
        # the response is fully sent (BackgroundTask runs post-send).
        _to_unlink: list[str] = []
        def _cleanup_temps():
            for _p in _to_unlink:
                try:
                    os.unlink(_p)
                except FileNotFoundError:
                    pass
                except Exception as _e:
                    logger.warning(f"Could not unlink temp PDF {_p}: {_e}")

        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)
            upload_id = find_source_upload_id(doc.current_content or "")
            if not upload_id:
                raise HTTPException(400, "Document is not linked to a source PDF")
            pdf_path = _locate_current_user_upload(request, upload_id, user)
            if not pdf_path:
                raise HTTPException(404, f"Source PDF {upload_id} not found")

            values = parse_markdown_to_values(doc.current_content or "")
            out_path = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
            _to_unlink.append(out_path)
            try:
                fill_fields(pdf_path, out_path, values)
            except Exception as e:
                logger.error(f"render_pdf fill_fields failed for {doc_id}: {e}")
                _cleanup_temps()
                raise HTTPException(500, f"PDF render failed: {e}")

            annotations = parse_markdown_annotations(doc.current_content or "")
            if annotations:
                ann_sig_ids = [
                    a["value"][len("signature:"):].strip()
                    for a in annotations
                    if a.get("kind") == "signature"
                    and isinstance(a.get("value"), str)
                    and a["value"].startswith("signature:")
                ]
                ann_signature_pngs: dict[str, bytes] = {}
                if ann_sig_ids:
                    # SECURITY: filter by owner so a caller can't reference
                    # someone else's signature ID from doc markdown and have
                    # it stamped/exported.
                    _sig_q = db.query(Signature).filter(Signature.id.in_(ann_sig_ids))
                    if user:
                        _sig_q = _sig_q.filter(Signature.owner == user)
                    sig_rows = _sig_q.all()
                    for s in sig_rows:
                        try:
                            ann_signature_pngs[s.id] = base64.b64decode(s.data_png)
                        except Exception as e:
                            logger.warning(f"Bad annotation signature data for {s.id}: {e}")
                annotated_path = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
                _to_unlink.append(annotated_path)
                try:
                    stamp_annotations(out_path, annotated_path, annotations, ann_signature_pngs)
                    out_path = annotated_path
                except Exception as e:
                    logger.error(f"stamp_annotations (render) failed for {doc_id}: {e}")

            return FileResponse(
                out_path,
                media_type="application/pdf",
                headers={"Content-Disposition": "inline"},
                background=BackgroundTask(_cleanup_temps),
            )
        finally:
            db.close()

    # ---- GET /api/document/{doc_id}/export-pdf ----
    @router.get("/api/document/{doc_id}/export-pdf")
    async def export_pdf(doc_id: str, request: Request):
        """Stream the filled PDF for download.

        Reads field values and signature selections from the markdown — there
        is no separate confirmation step. Signature fields contain their
        chosen signature ID encoded as `signature:<id>` in the value.
        """
        import base64
        import os
        import tempfile
        from fastapi.responses import FileResponse
        from starlette.background import BackgroundTask
        from src.pdf_form_doc import find_source_upload_id, parse_markdown_to_values, load_field_sidecar, parse_markdown_annotations
        from src.pdf_forms import fill_fields, stamp_signatures, stamp_annotations
        from core.database import Signature

        _to_unlink: list[str] = []
        def _cleanup_temps():
            for _p in _to_unlink:
                try:
                    os.unlink(_p)
                except FileNotFoundError:
                    pass
                except Exception as _e:
                    logger.warning(f"Could not unlink temp PDF {_p}: {_e}")

        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)

            upload_id = find_source_upload_id(doc.current_content or "")
            if not upload_id:
                raise HTTPException(400, "Document is not linked to a source PDF")

            pdf_path = _locate_current_user_upload(request, upload_id, user)
            if not pdf_path:
                raise HTTPException(404, f"Source PDF {upload_id} not found in uploads")

            schema = load_field_sidecar(pdf_path) or []
            sig_field_names = {f["name"] for f in schema if f.get("type") == "signature"}

            all_values = parse_markdown_to_values(doc.current_content or "")
            # Split: signature fields go to stamps, everything else to fill_fields
            text_values: dict = {}
            sig_ids: dict[str, str] = {}
            for name, raw in all_values.items():
                if name in sig_field_names and isinstance(raw, str) and raw.startswith("signature:"):
                    sig_ids[name] = raw[len("signature:"):].strip()
                elif name not in sig_field_names:
                    text_values[name] = raw

            stamps: dict = {}
            if sig_ids:
                # SECURITY: filter by owner — same reason as render_pdf.
                _sig_q2 = db.query(Signature).filter(Signature.id.in_(list(sig_ids.values())))
                if user:
                    _sig_q2 = _sig_q2.filter(Signature.owner == user)
                rows = _sig_q2.all()
                by_id = {s.id: s for s in rows}
                for field_name, sid in sig_ids.items():
                    s = by_id.get(sid)
                    if not s:
                        continue
                    try:
                        stamps[field_name] = base64.b64decode(s.data_png)
                    except Exception as e:
                        logger.warning(f"Bad signature data for {sid}: {e}")

            filled_path = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
            _to_unlink.append(filled_path)
            try:
                fill_fields(pdf_path, filled_path, text_values)
            except Exception as e:
                logger.error(f"fill_fields failed for doc {doc_id}: {e}")
                _cleanup_temps()
                raise HTTPException(500, f"PDF fill failed: {e}")

            out_path = filled_path
            if stamps:
                stamped_path = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
                _to_unlink.append(stamped_path)
                try:
                    stamp_signatures(filled_path, stamped_path, stamps)
                    out_path = stamped_path
                except Exception as e:
                    logger.error(f"stamp_signatures failed for doc {doc_id}: {e}")

            # Burn freeform annotations (Text/Check/Sign drops) on top.
            annotations = parse_markdown_annotations(doc.current_content or "")
            if annotations:
                # Resolve any signature annotations to their PNG bytes.
                ann_sig_ids = [
                    a["value"][len("signature:"):].strip()
                    for a in annotations
                    if a.get("kind") == "signature"
                    and isinstance(a.get("value"), str)
                    and a["value"].startswith("signature:")
                ]
                ann_signature_pngs: dict[str, bytes] = {}
                if ann_sig_ids:
                    # SECURITY: filter by owner so a caller can't reference
                    # someone else's signature ID from doc markdown and have
                    # it stamped/exported.
                    _sig_q = db.query(Signature).filter(Signature.id.in_(ann_sig_ids))
                    if user:
                        _sig_q = _sig_q.filter(Signature.owner == user)
                    sig_rows = _sig_q.all()
                    for s in sig_rows:
                        try:
                            ann_signature_pngs[s.id] = base64.b64decode(s.data_png)
                        except Exception as e:
                            logger.warning(f"Bad annotation signature data for {s.id}: {e}")
                annotated_path = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
                _to_unlink.append(annotated_path)
                try:
                    stamp_annotations(out_path, annotated_path, annotations, ann_signature_pngs)
                    out_path = annotated_path
                except Exception as e:
                    logger.error(f"stamp_annotations failed for doc {doc_id}: {e}")

            download_name = _slug(doc.title or "form") + "_annotated.pdf"
            return FileResponse(
                out_path,
                media_type="application/pdf",
                filename=download_name,
                background=BackgroundTask(_cleanup_temps),
            )
        finally:
            db.close()

    # ---- POST /api/document/{doc_id}/prepare-signed-reply ----
    @router.post("/api/document/{doc_id}/prepare-signed-reply")
    async def prepare_signed_reply(doc_id: str, request: Request):
        """Bake the current PDF state (form fields + signature stamps +
        annotations) into a flattened PDF, drop it in COMPOSE_UPLOADS_DIR
        and return the reply context (To/Subject/threading headers) so the
        frontend can open a reply draft with this attachment pre-loaded.

        Requires the document to have source_email_* metadata (set when the
        doc was created via /api/email/attachment-as-doc). Otherwise 400.
        """
        import base64
        import tempfile
        import shutil
        import uuid as _uuid
        import email as _email_mod
        from src.pdf_form_doc import (
            find_source_upload_id, parse_markdown_to_values,
            load_field_sidecar, parse_markdown_annotations,
        )
        from src.pdf_forms import fill_fields, stamp_signatures, stamp_annotations
        from core.database import Signature
        # COMPOSE_UPLOADS_DIR lives in email_routes — re-derive here so we
        # don't import from a routes file (cycle-prone). Same env override
        # as email_routes (ODYSSEUS_MAIL_ATTACHMENTS_DIR).
        from pathlib import Path as _Path
        import os as _os
        _DATA_DIR = _Path(__file__).resolve().parent.parent / "data"
        _BASE = _os.environ.get("ODYSSEUS_MAIL_ATTACHMENTS_DIR", str(_DATA_DIR / "mail-attachments"))
        _COMPOSE_DIR = _Path(_BASE) / "_compose"
        _COMPOSE_DIR.mkdir(parents=True, exist_ok=True)

        user = get_current_user(request)
        db = SessionLocal()
        try:
            doc = db.query(Document).filter(Document.id == doc_id).first()
            if not doc:
                raise HTTPException(404, "Document not found")
            _verify_doc_owner(db, doc, user)

            if not (doc.source_email_uid and doc.source_email_folder):
                raise HTTPException(400, "Document has no source email — cannot reply")

            # 1) Build the flattened PDF (same pipeline as export_pdf)
            upload_id = find_source_upload_id(doc.current_content or "")
            if not upload_id:
                raise HTTPException(400, "Document is not linked to a source PDF")
            pdf_path = _locate_current_user_upload(request, upload_id, user)
            if not pdf_path:
                raise HTTPException(404, f"Source PDF {upload_id} not found")

            schema = load_field_sidecar(pdf_path) or []
            sig_field_names = {f["name"] for f in schema if f.get("type") == "signature"}
            all_values = parse_markdown_to_values(doc.current_content or "")
            text_values: dict = {}
            sig_ids: dict[str, str] = {}
            for name, raw in all_values.items():
                if name in sig_field_names and isinstance(raw, str) and raw.startswith("signature:"):
                    sig_ids[name] = raw[len("signature:"):].strip()
                elif name not in sig_field_names:
                    text_values[name] = raw

            stamps: dict = {}
            if sig_ids:
                # SECURITY: filter by owner — same reason as render_pdf.
                _sig_q2 = db.query(Signature).filter(Signature.id.in_(list(sig_ids.values())))
                if user:
                    _sig_q2 = _sig_q2.filter(Signature.owner == user)
                rows = _sig_q2.all()
                by_id = {s.id: s for s in rows}
                for fname, sid in sig_ids.items():
                    s = by_id.get(sid)
                    if not s:
                        continue
                    try:
                        stamps[fname] = base64.b64decode(s.data_png)
                    except Exception:
                        pass

            import os
            _to_unlink: list[str] = []
            filled_path = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
            _to_unlink.append(filled_path)
            fill_fields(pdf_path, filled_path, text_values)
            out_path = filled_path
            if stamps:
                stamped_path = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
                _to_unlink.append(stamped_path)
                try:
                    stamp_signatures(filled_path, stamped_path, stamps)
                    out_path = stamped_path
                except Exception as e:
                    logger.warning(f"stamp_signatures failed for {doc_id}: {e}")

            annotations = parse_markdown_annotations(doc.current_content or "")
            if annotations:
                ann_sig_ids = [
                    a["value"][len("signature:"):].strip()
                    for a in annotations
                    if a.get("kind") == "signature"
                    and isinstance(a.get("value"), str)
                    and a["value"].startswith("signature:")
                ]
                ann_signature_pngs: dict[str, bytes] = {}
                if ann_sig_ids:
                    # SECURITY: filter by owner so a caller can't reference
                    # someone else's signature ID from doc markdown and have
                    # it stamped/exported.
                    _sig_q = db.query(Signature).filter(Signature.id.in_(ann_sig_ids))
                    if user:
                        _sig_q = _sig_q.filter(Signature.owner == user)
                    sig_rows = _sig_q.all()
                    for s in sig_rows:
                        try:
                            ann_signature_pngs[s.id] = base64.b64decode(s.data_png)
                        except Exception:
                            pass
                annotated_path = tempfile.NamedTemporaryFile(suffix=".pdf", delete=False).name
                _to_unlink.append(annotated_path)
                try:
                    stamp_annotations(out_path, annotated_path, annotations, ann_signature_pngs)
                    out_path = annotated_path
                except Exception as e:
                    logger.warning(f"stamp_annotations failed for {doc_id}: {e}")

            # 2) Move/copy into COMPOSE_UPLOADS_DIR with the token format
            #    `<uuid>_<original_name>` that /api/email/send expects.
            filename = _slug(doc.title or "signed") + "_signed.pdf"
            token = f"{_uuid.uuid4().hex}_{filename}"
            dest = _COMPOSE_DIR / token
            shutil.copyfile(out_path, str(dest))
            # Unlink the intermediate temp PDFs now that they've been
            # copied into COMPOSE_UPLOADS_DIR.
            for _p in _to_unlink:
                try:
                    os.unlink(_p)
                except FileNotFoundError:
                    pass
                except Exception as _e:
                    logger.warning(f"Could not unlink temp PDF {_p}: {_e}")

            # 3) Fetch the source email's headers so we can build a clean reply
            #    context (To/Subject/In-Reply-To/References).
            try:
                from routes.email_routes import _imap, _decode_header
            except Exception:
                _imap = None
                _decode_header = lambda x: x or ""

            to_addr = ""
            from_name = ""
            subject = ""
            in_reply_to = doc.source_email_message_id or ""
            references = in_reply_to
            if _imap:
                try:
                    with _imap(doc.source_email_account_id or None) as conn:
                        conn.select(doc.source_email_folder, readonly=True)
                        status, data = conn.fetch(doc.source_email_uid.encode(), "(RFC822.HEADER)")
                    if status == "OK" and data and data[0]:
                        raw_hdr = data[0][1]
                        m = _email_mod.message_from_bytes(raw_hdr)
                        sender = _decode_header(m.get("From", ""))
                        from_name, to_addr = _email_mod.utils.parseaddr(sender)
                        if not to_addr:
                            to_addr = sender
                        subject = _decode_header(m.get("Subject", "") or "")
                        if subject and not subject.lower().startswith("re:"):
                            subject = "Re: " + subject
                        msg_refs = (m.get("References") or "").strip()
                        msg_in_reply = (m.get("Message-ID") or "").strip() or in_reply_to
                        in_reply_to = msg_in_reply
                        references = (msg_refs + " " + msg_in_reply).strip() if msg_refs else msg_in_reply
                except Exception as e:
                    logger.warning(f"prepare-signed-reply header fetch failed: {e}")

            return {
                "ok": True,
                "attachment": {
                    "token": token,
                    "filename": filename,
                    "size": dest.stat().st_size,
                },
                "reply": {
                    "to": to_addr,
                    "to_name": from_name,
                    "subject": subject,
                    "in_reply_to": in_reply_to,
                    "references": references,
                    "account_id": doc.source_email_account_id or None,
                    "source_uid": doc.source_email_uid,
                    "source_folder": doc.source_email_folder,
                    "source_message_id": doc.source_email_message_id,
                },
            }
        finally:
            db.close()

    return router
