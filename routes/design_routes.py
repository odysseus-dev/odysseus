"""design_routes.py — Design Maker backend.

A dedicated design surface (Claude-Design style): projects group pages, each
page is backed by a Document(language="design") so generation, versioning
(DocumentVersion) and the Library are reused. Generation runs as a background
job streamed over SSE (same shape as Deep Research), so long LLM calls don't
block the request.

Also keeps the legacy ``POST /api/design`` used by the in-editor design path
until that surface is removed.
"""

import asyncio
import io
import json
import logging
import re
import time
import uuid
import zipfile
from typing import Optional

from fastapi import APIRouter, Request, Form
from fastapi.responses import StreamingResponse, Response

from src.auth_helpers import effective_user, require_privilege
from src.design_service import generate_design_html, save_new_design, save_design_version
from core.database import (
    SessionLocal,
    Document,
    DocumentVersion,
    DesignProject,
    DesignPage,
    DesignComment,
    DesignAsset,
    DesignSystem,
    DesignTemplate,
)

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api")

# In-process generation job registry (mirrors research's _active_tasks). Maps
# job_id -> {status: queued|running|done|error, kind, project_id, page_id,
# document_id, version, error, owner, task, created}. Jobs are evicted when the
# SSE stream reads their final state, and stale finished jobs are swept on the
# next _start_job so the dict can't grow unboundedly if no stream ever connects.
_DESIGN_JOBS: dict = {}
_JOB_TTL_TICKS = 600  # SSE poll ~0.8s -> ~8min ceiling before a stream gives up
_JOB_SWEEP_AGE = 300  # seconds a finished job lingers before _start_job sweeps it


def _sweep_jobs():
    now = time.monotonic()
    for jid, j in list(_DESIGN_JOBS.items()):
        if j.get("status") in ("done", "error") and (now - j.get("created", now)) > _JOB_SWEEP_AGE:
            _DESIGN_JOBS.pop(jid, None)


# Markup bridge — injected into the rendered design so the Design Maker can do
# element-anchored comments (data-cc-id) the way Claude Design does. The iframe
# is opaque-origin (sandbox=allow-scripts), so this talks to the parent ONLY via
# postMessage (allowed; not a network connection, so connect-src 'none' is fine).
_MARKUP_BRIDGE = """
<script id="dm-bridge">(function(){
  try{var els=document.querySelectorAll('body *');for(var i=0;i<els.length;i++){if(!els[i].hasAttribute('data-cc-id'))els[i].setAttribute('data-cc-id','cc-'+i);}}catch(e){}
  var MARK=false,last=null,EDIT=false,SEL=null;
  function vw(){return Math.max(document.documentElement.clientWidth,1);}
  function vh(){return Math.max(document.documentElement.clientHeight,1);}
  function label(el){var t=(el.getAttribute('aria-label')||el.innerText||el.getAttribute('alt')||'').replace(/\\s+/g,' ').trim();if(t.length>48)t=t.slice(0,48)+'\\u2026';var g=el.tagName.toLowerCase();return t?g+' \\u201c'+t+'\\u201d':g;}
  function clearHi(){if(last){last.style.outline='';last.style.outlineOffset='';last=null;}}
  function center(el){var r=el.getBoundingClientRect();return {x:(r.left+r.width/2)/vw(),y:(r.top+r.height/2)/vh()};}
  function clean(){
    var root=document.documentElement.cloneNode(true);
    var b=root.querySelector('#dm-bridge');if(b&&b.parentNode)b.parentNode.removeChild(b);
    var cc=root.querySelectorAll('[data-cc-id]');for(var i=0;i<cc.length;i++)cc[i].removeAttribute('data-cc-id');
    var ce=root.querySelectorAll('[contenteditable]');for(var j=0;j<ce.length;j++)ce[j].removeAttribute('contenteditable');
    var st=root.querySelectorAll('[style]');for(var k=0;k<st.length;k++){st[k].style.outline='';st[k].style.outlineOffset='';if(!st[k].getAttribute('style'))st[k].removeAttribute('style');}
    return '<!DOCTYPE html>\\n'+root.outerHTML;
  }
  // --- Element property panel (edit mode) ---------------------------------
  function selOutline(el,on){if(!el)return;if(on){el.style.outline='2px solid #4a9eff';el.style.outlineOffset='1px';}else{el.style.outline='';el.style.outlineOffset='';}}
  function fam(cs){var f=(cs.fontFamily||'').split(',')[0];return f.replace(/['\"]/g,'').trim();}
  function selectEl(el){
    if(SEL&&SEL!==el)selOutline(SEL,false);
    SEL=el;selOutline(el,true);
    var cs=window.getComputedStyle(el);
    parent.postMessage({type:'__dm_sel',ccId:el.getAttribute('data-cc-id'),styles:{fontSize:parseInt(cs.fontSize,10)||0,color:cs.color,fontWeight:cs.fontWeight,fontFamily:fam(cs),textAlign:cs.textAlign}},'*');
  }
  // While editing, keep nav keys (Space/Arrows/etc) from reaching the design's
  // own document/window handlers (e.g. slide-deck advance) — but DON'T
  // preventDefault, so the keys still edit text normally.
  var NAVKEYS={' ':1,'Spacebar':1,'ArrowLeft':1,'ArrowRight':1,'ArrowUp':1,'ArrowDown':1,'PageUp':1,'PageDown':1,'Home':1,'End':1,'Enter':1};
  function keyGuard(e){if(!EDIT)return;if(!NAVKEYS[e.key])return;var t=e.target;if(t&&t.isContentEditable){e.stopPropagation();e.stopImmediatePropagation();}}
  window.addEventListener('message',function(e){
    var d=e.data||{};
    if(d.type==='__dm_markup'){MARK=!!d.on;document.documentElement.style.cursor=MARK?'crosshair':'';if(!MARK)clearHi();}
    else if(d.type==='__dm_edit'){
      if(d.on){EDIT=true;MARK=false;document.documentElement.style.cursor='';clearHi();document.body.contentEditable='true';document.body.style.outline='2px dashed #4a9eff';document.body.style.outlineOffset='-2px';}
      else{EDIT=false;if(SEL){selOutline(SEL,false);SEL=null;}document.body.contentEditable='false';document.body.style.outline='';document.body.style.outlineOffset='';parent.postMessage({type:'__dm_edit_html',html:clean()},'*');}
    }
    else if(d.type==='__dm_style'&&SEL){
      var p=d.prop,v=d.value;
      if(p==='fontSize')SEL.style.fontSize=v;
      else if(p==='color')SEL.style.color=v;
      else if(p==='fontWeight')SEL.style.fontWeight=v;
      else if(p==='fontFamily')SEL.style.fontFamily=v;
      else if(p==='textAlign')SEL.style.textAlign=v;
    }
    else if(d.type==='__dm_relocate'&&d.ids){var out={};for(var i=0;i<d.ids.length;i++){var el=document.querySelector('[data-cc-id=\"'+d.ids[i]+'\"]');if(el)out[d.ids[i]]=center(el);}parent.postMessage({type:'__dm_located',pos:out},'*');}
  });
  document.addEventListener('mouseover',function(e){if(!MARK)return;clearHi();last=e.target;last.style.outline='2px solid #e06c75';last.style.outlineOffset='1px';},true);
  document.addEventListener('click',function(e){if(!MARK)return;e.preventDefault();e.stopPropagation();var el=e.target,c=center(el);parent.postMessage({type:'__dm_pick',ccId:el.getAttribute('data-cc-id'),label:label(el),x:c.x,y:c.y},'*');},true);
  // Edit mode: select the clicked element (don't preventDefault — caret/typing
  // must keep working). body stays contentEditable, so text is still typable.
  document.addEventListener('click',function(e){if(!EDIT)return;var el=e.target;if(!el||el.nodeType!==1||el===document.body||el===document.documentElement)return;selectEl(el);},true);
  document.addEventListener('keydown',keyGuard,true);
  document.addEventListener('keyup',keyGuard,true);
  document.addEventListener('keypress',keyGuard,true);
})();</script>
"""


def _inject_bridge(html: str) -> str:
    html = html or ""
    low = html.lower()
    i = low.rfind("</body>")
    if i != -1:
        return html[:i] + _MARKUP_BRIDGE + html[i:]
    return html + _MARKUP_BRIDGE


def _uid() -> str:
    return str(uuid.uuid4())


def _safe_name(s: str, fallback: str = "design") -> str:
    s = re.sub(r"[^A-Za-z0-9_-]+", "_", (s or "").strip())
    return s[:60] or fallback


# ---------------------------------------------------------------------------
# Serialization
# ---------------------------------------------------------------------------
def _page_dict(db, page: DesignPage, with_content: bool = False) -> dict:
    doc = db.query(Document).filter(Document.id == page.document_id).first() if page.document_id else None
    d = {
        "id": page.id,
        "project_id": page.project_id,
        "document_id": page.document_id,
        "title": page.title,
        "order_index": page.order_index,
        "version": (doc.version_count if doc else 0),
    }
    if with_content:
        d["content"] = (doc.current_content if doc else "") or ""
    return d


def _comment_dict(c: DesignComment) -> dict:
    return {
        "id": c.id,
        "project_id": c.project_id,
        "page_id": c.page_id,
        "version_id": c.version_id,
        "anchor": json.loads(c.anchor) if c.anchor else None,
        "body": c.body or "",
        "author": c.author,
        "resolved": bool(c.resolved),
    }


def _project_dict(db, p: DesignProject, full: bool = False) -> dict:
    d = {
        "id": p.id,
        "name": p.name,
        "owner": p.owner,
        "cover_page_id": p.cover_page_id,
        "settings": json.loads(p.settings) if p.settings else {},
        "archived": bool(p.archived),
        "page_count": db.query(DesignPage).filter(DesignPage.project_id == p.id).count(),
        "updated_at": p.updated_at.isoformat() if getattr(p, "updated_at", None) else None,
    }
    if full:
        pages = db.query(DesignPage).filter(DesignPage.project_id == p.id).order_by(DesignPage.order_index).all()
        d["pages"] = [_page_dict(db, pg, with_content=True) for pg in pages]
        comments = db.query(DesignComment).filter(DesignComment.project_id == p.id).all()
        d["comments"] = [_comment_dict(c) for c in comments]
        assets = db.query(DesignAsset).filter(DesignAsset.project_id == p.id).all()
        d["assets"] = [{"id": a.id, "kind": a.kind, "name": a.name} for a in assets]
    return d


# ---------------------------------------------------------------------------
# Ownership helpers (owner-scoped; single-user owner=None still works)
# ---------------------------------------------------------------------------
def _get_project(db, pid: str, owner) -> Optional[DesignProject]:
    p = db.query(DesignProject).filter(DesignProject.id == pid).first()
    if p and owner is not None and p.owner != owner:
        return None
    return p


def _get_page(db, page_id: str, owner) -> Optional[DesignPage]:
    pg = db.query(DesignPage).filter(DesignPage.id == page_id).first()
    if not pg:
        return None
    proj = _get_project(db, pg.project_id, owner)
    return pg if proj else None


def _not_found():
    return Response(content=json.dumps({"error": "not found"}),
                    media_type="application/json", status_code=404)


# ---------------------------------------------------------------------------
# Storage helper (no chat session required — unlike save_new_design)
# ---------------------------------------------------------------------------
def _create_design_document(db, html: str, title: str, owner) -> str:
    # Start at version_count=0 with NO initial version so the FIRST generation
    # becomes v1 (save_design_version does version_count+1). Avoids a junk empty
    # v1 in the history that users could revert to and blank the page.
    has_content = bool((html or "").strip())
    doc_id = _uid()
    doc = Document(
        id=doc_id, session_id=None, title=(title or "Page"),
        language="design", current_content=html or "",
        version_count=(1 if has_content else 0),
        is_active=False, owner=owner,
    )
    db.add(doc)
    if has_content:
        db.add(DocumentVersion(
            id=_uid(), document_id=doc_id, version_number=1,
            content=html, summary="Created by Design Maker", source="ai",
        ))
    return doc_id


# ---------------------------------------------------------------------------
# Generation job (background + SSE)
# ---------------------------------------------------------------------------
def _doc_content(document_id: str) -> Optional[str]:
    db = SessionLocal()
    try:
        doc = db.query(Document).filter(Document.id == document_id).first()
        return doc.current_content if doc else None
    finally:
        db.close()


def _design_system_spec(ds_id: Optional[str], owner) -> Optional[str]:
    """Load a DesignSystem's spec text (owner-scoped) for a generation, or None."""
    if not ds_id:
        return None
    db = SessionLocal()
    try:
        ds = db.query(DesignSystem).filter(DesignSystem.id == ds_id).first()
        if not ds or (owner is not None and ds.owner != owner):
            return None
        return (ds.spec or "").strip() or None
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Attachments — transient generation context (images, text, URLs). v1 keeps no
# persistent storage; the parsed material is folded into the generation request.
# ---------------------------------------------------------------------------
_MAX_IMAGES = 4
_MAX_IMAGE_DATAURL_BYTES = 4 * 1024 * 1024  # skip a single image whose data URL > ~4MB
_MAX_REF_URLS = 3
_REF_PER_URL_CHARS = 8000
_REF_TOTAL_CHARS = 24000


def _parse_images(images: str) -> list:
    """Parse a JSON array of data: URLs into a capped, sanitized list. Skips
    non-data and oversized entries; never raises (returns [] on bad JSON)."""
    if not images:
        return []
    try:
        arr = json.loads(images)
    except Exception:
        return []
    out: list = []
    if isinstance(arr, list):
        for u in arr:
            if not isinstance(u, str) or not u.startswith("data:"):
                continue
            if len(u) > _MAX_IMAGE_DATAURL_BYTES:
                logger.info("design attachment: skipping oversized image (%d bytes)", len(u))
                continue
            out.append(u)
            if len(out) >= _MAX_IMAGES:
                break
    return out


def _github_raw(url: str) -> str:
    """For github.com blob URLs, prefer the raw text endpoint so we fetch the
    file source instead of the HTML page chrome."""
    try:
        if "github.com" in url and "/blob/" in url:
            return (url.replace("github.com", "raw.githubusercontent.com")
                       .replace("/blob/", "/", 1))
    except Exception:
        pass
    return url


async def _build_reference(reference_text: str, attachment_urls: str, owner) -> str:
    """Assemble the transient REFERENCE CONTEXT for a generation: the user's
    pasted/file text + the extracted text of any attached URLs. Robust by design
    — a failing URL is skipped with a note, never raises. ``owner`` is accepted
    for parity/future scoping. Caps each URL and the total to keep context sane."""
    parts: list = []
    base = (reference_text or "").strip()
    if base:
        parts.append(base)
    urls: list = []
    if attachment_urls:
        try:
            arr = json.loads(attachment_urls)
            if isinstance(arr, list):
                urls = [str(u).strip() for u in arr if str(u or "").strip()][:_MAX_REF_URLS]
        except Exception:
            urls = []
    for u in urls:
        text = ""
        try:
            from src.search import fetch_webpage_content
            page = await asyncio.to_thread(fetch_webpage_content, _github_raw(u), 15)
            if page and page.get("success") and page.get("content"):
                text = str(page["content"]).strip()
        except Exception as e:
            logger.info("design reference fetch failed for %s: %s", u, e)
            text = ""
        if text:
            parts.append(f"--- Reference from {u} ---\n{text[:_REF_PER_URL_CHARS]}")
        else:
            parts.append(f"--- Reference from {u} (could not be fetched) ---")
    combined = "\n\n".join(p for p in parts if p)
    return combined[:_REF_TOTAL_CHARS]


async def _run_design_agent(job_id: str, *, page_id: str, document_id: str,
                            prompt: str, model: Optional[str], owner, mode: str,
                            design_system_id: Optional[str] = None,
                            reference_text: str = "", images: Optional[list] = None):
    """Agentic generation: plan (brief + todos) -> build -> self-critique -> fix,
    streaming each step as an event. Edits take the fast single-pass path.
    Clarifying answers are folded into `prompt` by the caller."""
    from src.design_service import (
        generate_design_html, save_design_version, plan_design, critique_design,
    )
    job = _DESIGN_JOBS.get(job_id)
    if job is None:
        return
    job["status"] = "running"
    job.setdefault("events", [])

    def emit(ev):
        job["events"].append(ev)

    async def _save(html):
        res = await save_design_version(html, {"owner": owner, "doc_id": document_id})
        if res.get("error"):
            raise RuntimeError(res["error"])
        return res.get("version")

    try:
        # ---- Edit: fast single pass (no full brief/critique loop) ----
        if mode == "edit":
            emit({"type": "phase", "phase": "build", "text": "Aplicando sua edição…"})
            html = await generate_design_html(prompt, _doc_content(document_id), model, owner,
                                              reference_text=reference_text, images=images)
            v = await _save(html)
            emit({"type": "version", "version": v})
            job["status"] = "done"
            job["version"] = v
            emit({"type": "done"})
            return

        # ---- Create: full design agent ----
        ds_spec = _design_system_spec(design_system_id, owner)
        emit({"type": "phase", "phase": "plan", "text": "Definindo a direção de design…"})
        plan = await plan_design(prompt, model, owner, design_system=ds_spec,
                                 reference_text=reference_text, images=images)
        if plan.get("brief"):
            emit({"type": "brief", "text": plan["brief"], "system": plan.get("system") or {}})
        todos = plan.get("todos") or []
        emit({"type": "todos", "items": todos})

        emit({"type": "phase", "phase": "build", "text": "Construindo o design…"})
        html = await generate_design_html(prompt, None, model, owner, brief=plan,
                                          design_system=ds_spec,
                                          reference_text=reference_text, images=images)
        v = await _save(html)
        for i in range(len(todos)):
            emit({"type": "todo_done", "index": i})
        emit({"type": "version", "version": v})

        emit({"type": "phase", "phase": "critique", "text": "Revisando a qualidade…"})
        crit = await critique_design(html, model, owner)
        emit({"type": "critique", "verdict": crit.get("verdict"), "issues": crit.get("issues") or []})
        if crit.get("verdict") == "revise" and crit.get("issues"):
            emit({"type": "phase", "phase": "fix", "text": "Aplicando melhorias…"})
            fix_prompt = ("Apply these design-director fixes precisely; keep everything else:\n"
                          + "\n".join("- " + i for i in crit["issues"]))
            try:
                html2 = await generate_design_html(fix_prompt, html, model, owner)
                v = await _save(html2)
                emit({"type": "version", "version": v})
            except Exception as e:
                logger.info("design fix pass skipped: %s", e)

        job["status"] = "done"
        job["version"] = v
        emit({"type": "done"})
    except Exception as e:
        logger.warning("design agent job %s failed: %s", job_id, e)
        job["status"] = "error"
        job["error"] = str(e)
        emit({"type": "error", "text": str(e)})


def _start_job(*, kind: str, project_id: str, page_id: str, document_id: str,
               prompt: str, model: Optional[str], owner, mode: str,
               design_system_id: Optional[str] = None,
               reference_text: str = "", images: Optional[list] = None) -> str:
    _sweep_jobs()
    job_id = _uid()
    _DESIGN_JOBS[job_id] = {
        "status": "queued", "kind": kind, "project_id": project_id,
        "page_id": page_id, "document_id": document_id, "owner": owner,
        "version": None, "error": None, "created": time.monotonic(), "events": [],
    }
    # Keep a reference to the task — asyncio only holds a weak ref, so a
    # fire-and-forget task can be GC'd mid-flight (see research_handler).
    _DESIGN_JOBS[job_id]["task"] = asyncio.create_task(
        _run_design_agent(job_id, page_id=page_id, document_id=document_id,
                          prompt=prompt, model=model, owner=owner, mode=mode,
                          design_system_id=design_system_id,
                          reference_text=reference_text, images=images))
    return job_id


@router.get("/design/job/{job_id}/stream")
async def design_job_stream(request: Request, job_id: str):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None

    async def _gen():
        ticks = 0
        sent = 0
        while True:
            job = _DESIGN_JOBS.get(job_id)
            if not job:
                yield f'data: {json.dumps({"status": "error", "error": "job desconhecido", "final": True})}\n\n'
                return
            if owner is not None and job.get("owner") not in (None, owner):
                yield f'data: {json.dumps({"status": "error", "error": "forbidden", "final": True})}\n\n'
                return
            # Stream any new agent events (brief, todos, version, critique…).
            evs = job.get("events", [])
            while sent < len(evs):
                yield f'data: {json.dumps({"type": "event", "status": job["status"], "page_id": job["page_id"], "project_id": job["project_id"], "event": evs[sent]})}\n\n'
                sent += 1
            if job["status"] in ("done", "error"):
                payload = {"status": job["status"], "job_id": job_id,
                           "project_id": job["project_id"], "page_id": job["page_id"], "final": True}
                if job["status"] == "done":
                    payload["version"] = job.get("version")
                else:
                    payload["error"] = job.get("error")
                yield f'data: {json.dumps(payload)}\n\n'
                _DESIGN_JOBS.pop(job_id, None)  # consumed — free it
                return
            ticks += 1
            if ticks > _JOB_TTL_TICKS:
                yield f'data: {json.dumps({"status": "error", "error": "timeout", "final": True})}\n\n'
                return
            await asyncio.sleep(0.5)

    return StreamingResponse(_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


@router.post("/design/clarify")
async def design_clarify(request: Request, prompt: str = Form(...), model: str = Form("")):
    """Return up to 3 clarifying questions for a vague request, or [] to proceed.
    Called before a create generation so the agent can ask like Claude Design."""
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    from src.design_service import clarify_design
    try:
        questions = await clarify_design(prompt.strip(), model or None, owner)
    except Exception as e:
        logger.info("clarify endpoint error: %s", e)
        questions = []
    return {"questions": questions}


# ---------------------------------------------------------------------------
# Projects
# ---------------------------------------------------------------------------
@router.post("/design/project")
async def create_project(request: Request, name: str = Form(""), prompt: str = Form(""),
                         model: str = Form(""), design_system_id: str = Form(""),
                         reference_text: str = Form(""), attachment_urls: str = Form(""),
                         images: str = Form("")):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        pid = _uid()
        proj = DesignProject(id=pid, name=(name.strip() or "Untitled design"), owner=owner)
        db.add(proj)
        out = {"project_id": pid}
        if prompt.strip():
            doc_id = _create_design_document(db, "", (prompt.strip()[:60] or "Page 1"), owner)
            page = DesignPage(id=_uid(), project_id=pid, document_id=doc_id,
                              title=(prompt.strip()[:60] or "Page 1"), order_index=0)
            db.add(page)
            proj.cover_page_id = page.id
            db.commit()
            ref = await _build_reference(reference_text, attachment_urls, owner)
            imgs = _parse_images(images)
            job_id = _start_job(kind="create", project_id=pid, page_id=page.id,
                                document_id=doc_id, prompt=prompt.strip(),
                                model=model or None, owner=owner, mode="create",
                                design_system_id=(design_system_id or None),
                                reference_text=ref, images=imgs)
            out["page_id"] = page.id
            out["job_id"] = job_id
        else:
            db.commit()
        return out
    except Exception as e:
        db.rollback()
        logger.warning("create_project failed: %s", e)
        return Response(content=json.dumps({"error": str(e)}), media_type="application/json", status_code=500)
    finally:
        db.close()


@router.get("/design/projects")
async def list_projects(request: Request, include_archived: bool = False):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        q = db.query(DesignProject)
        if owner is not None:
            q = q.filter(DesignProject.owner == owner)
        if not include_archived:
            q = q.filter(DesignProject.archived == False)  # noqa: E712
        q = q.order_by(DesignProject.updated_at.desc())
        return {"projects": [_project_dict(db, p) for p in q.all()]}
    finally:
        db.close()


@router.get("/design/project/{pid}")
async def get_project(request: Request, pid: str):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        p = _get_project(db, pid, owner)
        if not p:
            return _not_found()
        return _project_dict(db, p, full=True)
    finally:
        db.close()


@router.patch("/design/project/{pid}")
async def update_project(request: Request, pid: str, name: str = Form(None),
                         settings: str = Form(None), cover_page_id: str = Form(None),
                         archived: str = Form(None)):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        p = _get_project(db, pid, owner)
        if not p:
            return _not_found()
        if name is not None:
            p.name = name.strip() or p.name
        if settings is not None:
            try:
                json.loads(settings)
                p.settings = settings
            except Exception:
                pass
        if cover_page_id is not None:
            p.cover_page_id = cover_page_id or None
        if archived is not None:
            p.archived = str(archived).lower() == "true"
        db.commit()
        return _project_dict(db, p)
    finally:
        db.close()


@router.delete("/design/project/{pid}")
async def delete_project(request: Request, pid: str):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        p = _get_project(db, pid, owner)
        if not p:
            return _not_found()
        doc_ids = [pg.document_id for pg in db.query(DesignPage).filter(DesignPage.project_id == pid).all()]
        db.delete(p)
        if doc_ids:
            db.query(Document).filter(Document.id.in_(doc_ids)).delete(synchronize_session=False)
        db.commit()
        return {"deleted": pid}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------
@router.post("/design/project/{pid}/page")
async def create_page(request: Request, pid: str, prompt: str = Form(...),
                      title: str = Form(""), model: str = Form(""),
                      design_system_id: str = Form(""),
                      reference_text: str = Form(""), attachment_urls: str = Form(""),
                      images: str = Form("")):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        proj = _get_project(db, pid, owner)
        if not proj:
            return _not_found()
        n = db.query(DesignPage).filter(DesignPage.project_id == pid).count()
        ptitle = title.strip() or prompt.strip()[:60] or f"Page {n+1}"
        doc_id = _create_design_document(db, "", ptitle, owner)
        page_id = _uid()
        db.add(DesignPage(id=page_id, project_id=pid, document_id=doc_id,
                          title=ptitle, order_index=n))
        if not proj.cover_page_id:
            proj.cover_page_id = page_id
        db.commit()
    finally:
        db.close()
    # Fetch attachment URLs AFTER releasing the DB connection (can take seconds).
    ref = await _build_reference(reference_text, attachment_urls, owner)
    imgs = _parse_images(images)
    job_id = _start_job(kind="create", project_id=pid, page_id=page_id,
                        document_id=doc_id, prompt=prompt.strip(),
                        model=model or None, owner=owner, mode="create",
                        design_system_id=(design_system_id or None),
                        reference_text=ref, images=imgs)
    return {"page_id": page_id, "job_id": job_id}


@router.post("/design/page/{page_id}/generate")
async def generate_page(request: Request, page_id: str, prompt: str = Form(...),
                        model: str = Form(""), mode: str = Form("edit"),
                        design_system_id: str = Form(""),
                        reference_text: str = Form(""), attachment_urls: str = Form(""),
                        images: str = Form("")):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        page = _get_page(db, page_id, owner)
        if not page:
            return _not_found()
        doc_id = page.document_id
        pid = page.project_id
    finally:
        db.close()
    ref = await _build_reference(reference_text, attachment_urls, owner)
    imgs = _parse_images(images)
    job_id = _start_job(kind=mode, project_id=pid, page_id=page_id, document_id=doc_id,
                        prompt=prompt.strip(), model=model or None, owner=owner,
                        mode=("edit" if mode == "edit" else "create"),
                        design_system_id=(design_system_id or None),
                        reference_text=ref, images=imgs)
    return {"job_id": job_id, "page_id": page_id}


@router.get("/design/page/{page_id}/render")
async def render_page(request: Request, page_id: str):
    """Standalone HTML for the canvas iframe. The security CSP is applied by
    SecurityHeadersMiddleware (is_design_render branch) so the design's CDN
    assets load while the opaque-origin sandbox stays locked down."""
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        page = _get_page(db, page_id, owner)
        if not page:
            return Response(content="<!doctype html><meta charset='utf-8'><title>404</title>",
                            media_type="text/html", status_code=404)
        doc = db.query(Document).filter(Document.id == page.document_id).first() if page.document_id else None
        html = (doc.current_content if doc else "") or "<!doctype html><html><body></body></html>"
    finally:
        db.close()
    return Response(content=_inject_bridge(html), media_type="text/html; charset=utf-8")


@router.post("/design/page/{page_id}/save-html")
async def save_page_html(request: Request, page_id: str, html: str = Form(...)):
    """Persist inline (edit-mode) canvas changes as a new design version. The
    cleaned HTML is serialized client-side by the markup bridge's clean()."""
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        page = _get_page(db, page_id, owner)
        if not page:
            return _not_found()
        document_id = page.document_id
    finally:
        db.close()
    res = await save_design_version(html, {"owner": owner, "doc_id": document_id})
    if res.get("error"):
        return Response(content=json.dumps({"error": res["error"]}),
                        media_type="application/json", status_code=500)
    return {"version": res.get("version"), "page_id": page_id}


@router.get("/design/page/{page_id}/versions")
async def list_versions(request: Request, page_id: str):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        page = _get_page(db, page_id, owner)
        if not page:
            return _not_found()
        vers = (db.query(DocumentVersion)
                .filter(DocumentVersion.document_id == page.document_id)
                .order_by(DocumentVersion.version_number.desc()).all())
        return {"versions": [{
            "id": v.id,
            "version_number": v.version_number,
            "summary": v.summary or "",
            "source": v.source or "",
            "length": len(v.content or ""),
            "created_at": v.created_at.isoformat() if getattr(v, "created_at", None) else None,
        } for v in vers]}
    finally:
        db.close()


@router.post("/design/page/{page_id}/revert")
async def revert_version(request: Request, page_id: str, version_id: str = Form(...)):
    """Restore a past version's content as a NEW version (non-destructive),
    mirroring save_design_version's version bump."""
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        page = _get_page(db, page_id, owner)
        if not page:
            return _not_found()
        doc = db.query(Document).filter(Document.id == page.document_id).first()
        if not doc:
            return _not_found()
        target = (db.query(DocumentVersion)
                  .filter(DocumentVersion.id == version_id,
                          DocumentVersion.document_id == doc.id).first())
        if not target:
            return _not_found()
        new_ver = doc.version_count + 1
        db.add(DocumentVersion(
            id=_uid(), document_id=doc.id, version_number=new_ver,
            content=target.content, summary=f"Revertido para v{target.version_number}",
            source="revert",
        ))
        doc.current_content = target.content
        doc.version_count = new_ver
        db.commit()
        return {"version": new_ver, "page_id": page_id}
    finally:
        db.close()


@router.patch("/design/page/{page_id}")
async def update_page(request: Request, page_id: str, title: str = Form(None),
                      order_index: int = Form(None)):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        page = _get_page(db, page_id, owner)
        if not page:
            return _not_found()
        if title is not None:
            page.title = title.strip() or page.title
        if order_index is not None:
            page.order_index = int(order_index)
        db.commit()
        return _page_dict(db, page)
    finally:
        db.close()


@router.delete("/design/page/{page_id}")
async def delete_page(request: Request, page_id: str):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        page = _get_page(db, page_id, owner)
        if not page:
            return _not_found()
        doc_id = page.document_id
        db.delete(page)
        if doc_id:
            db.query(Document).filter(Document.id == doc_id).delete(synchronize_session=False)
        db.commit()
        return {"deleted": page_id}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Comments / markup
# ---------------------------------------------------------------------------
@router.post("/design/page/{page_id}/comment")
async def add_comment(request: Request, page_id: str, body: str = Form(""),
                      anchor: str = Form("")):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        page = _get_page(db, page_id, owner)
        if not page:
            return _not_found()
        anchor_val = None
        if anchor:
            try:
                json.loads(anchor)
                anchor_val = anchor
            except Exception:
                anchor_val = None
        c = DesignComment(id=_uid(), project_id=page.project_id, page_id=page_id,
                          anchor=anchor_val, body=body or "", author=owner, resolved=False)
        db.add(c)
        db.commit()
        return _comment_dict(c)
    finally:
        db.close()


@router.patch("/design/comment/{cid}")
async def update_comment(request: Request, cid: str, body: str = Form(None),
                         resolved: str = Form(None)):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        c = db.query(DesignComment).filter(DesignComment.id == cid).first()
        if c and owner is not None and not _get_project(db, c.project_id, owner):
            c = None
        if not c:
            return _not_found()
        if body is not None:
            c.body = body
        if resolved is not None:
            c.resolved = str(resolved).lower() == "true"
        db.commit()
        return _comment_dict(c)
    finally:
        db.close()


@router.delete("/design/comment/{cid}")
async def delete_comment(request: Request, cid: str):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        c = db.query(DesignComment).filter(DesignComment.id == cid).first()
        if c and owner is not None and not _get_project(db, c.project_id, owner):
            c = None
        if not c:
            return _not_found()
        db.delete(c)
        db.commit()
        return {"deleted": cid}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Export (zip of all pages' HTML)
# ---------------------------------------------------------------------------
@router.get("/design/project/{pid}/export")
async def export_project(request: Request, pid: str):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        p = _get_project(db, pid, owner)
        if not p:
            return _not_found()
        pages = db.query(DesignPage).filter(DesignPage.project_id == pid).order_by(DesignPage.order_index).all()
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            seen = set()
            for i, pg in enumerate(pages):
                doc = db.query(Document).filter(Document.id == pg.document_id).first()
                base = _safe_name(pg.title, f"page-{i+1}")
                fname = f"{i+1:02d}-{base}.html"
                if fname in seen:
                    fname = f"{i+1:02d}-{base}-{pg.id[:6]}.html"
                seen.add(fname)
                zf.writestr(fname, (doc.current_content if doc else "") or "")
        buf.seek(0)
        dl = _safe_name(p.name, "design") + ".zip"
        return Response(content=buf.read(), media_type="application/zip",
                        headers={"Content-Disposition": f'attachment; filename="{dl}"'})
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Design systems (reusable brand/style guides) + Templates (starting HTML)
# ---------------------------------------------------------------------------
_DESIGN_SYSTEM_EXTRACT_PROMPT = (
    "You are a senior brand/design-system analyst. Given one or more rendered HTML "
    "designs, reverse-engineer a concise, reusable DESIGN SYSTEM that captures the "
    "visual language so future pages can be built to match it.\n"
    "Return PLAIN TEXT only (no markdown fences, no preamble), as short labeled sections:\n"
    "- Fonts: heading vs body font families (Google Fonts names if recognizable).\n"
    "- Color palette: each role (background, surface, text, muted, accent, border, etc.) "
    "with hex AND oklch when inferable.\n"
    "- Spacing & layout: spacing rhythm, container widths, border radius, density.\n"
    "- Components: notable component patterns (buttons, cards, nav, inputs) and their style.\n"
    "- Voice: the copy tone.\n"
    "- Do / Don't: a few concrete guardrails to keep designs on-brand.\n"
    "Be specific and concise — this is authoritative guidance, not prose."
)


def _get_system(db, sid: str, owner) -> Optional[DesignSystem]:
    s = db.query(DesignSystem).filter(DesignSystem.id == sid).first()
    if s and owner is not None and s.owner != owner:
        return None
    return s


def _get_template(db, tid: str, owner) -> Optional[DesignTemplate]:
    t = db.query(DesignTemplate).filter(DesignTemplate.id == tid).first()
    if t and owner is not None and t.owner != owner:
        return None
    return t


def _system_dict(s: DesignSystem, full: bool = False) -> dict:
    d = {
        "id": s.id,
        "name": s.name,
        "updated_at": s.updated_at.isoformat() if getattr(s, "updated_at", None) else None,
    }
    if full:
        d["spec"] = s.spec or ""
        d["tokens"] = s.tokens
        d["archived"] = bool(s.archived)
    return d


def _template_dict(t: DesignTemplate, full: bool = False) -> dict:
    d = {
        "id": t.id,
        "name": t.name,
        "kind": t.kind,
        "updated_at": t.updated_at.isoformat() if getattr(t, "updated_at", None) else None,
    }
    if full:
        d["html"] = t.html or ""
    return d


@router.post("/design/system")
async def create_system(request: Request, name: str = Form(""), spec: str = Form(""),
                        tokens: str = Form("")):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        tok = None
        if tokens.strip():
            try:
                json.loads(tokens)
                tok = tokens
            except Exception:
                tok = None
        sid = _uid()
        s = DesignSystem(id=sid, name=(name.strip() or "Design system"), owner=owner,
                         spec=spec or "", tokens=tok)
        db.add(s)
        db.commit()
        return _system_dict(s, full=True)
    finally:
        db.close()


@router.get("/design/systems")
async def list_systems(request: Request, include_archived: bool = False):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        q = db.query(DesignSystem)
        if owner is not None:
            q = q.filter(DesignSystem.owner == owner)
        if not include_archived:
            q = q.filter(DesignSystem.archived == False)  # noqa: E712
        q = q.order_by(DesignSystem.updated_at.desc())
        return {"systems": [_system_dict(s) for s in q.all()]}
    finally:
        db.close()


@router.get("/design/system/{sid}")
async def get_system(request: Request, sid: str):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        s = _get_system(db, sid, owner)
        if not s:
            return _not_found()
        return _system_dict(s, full=True)
    finally:
        db.close()


@router.patch("/design/system/{sid}")
async def update_system(request: Request, sid: str, name: str = Form(None),
                        spec: str = Form(None), tokens: str = Form(None),
                        archived: str = Form(None)):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        s = _get_system(db, sid, owner)
        if not s:
            return _not_found()
        if name is not None:
            s.name = name.strip() or s.name
        if spec is not None:
            s.spec = spec
        if tokens is not None:
            if tokens.strip():
                try:
                    json.loads(tokens)
                    s.tokens = tokens
                except Exception:
                    pass
            else:
                s.tokens = None
        if archived is not None:
            s.archived = str(archived).lower() == "true"
        db.commit()
        return _system_dict(s, full=True)
    finally:
        db.close()


@router.delete("/design/system/{sid}")
async def delete_system(request: Request, sid: str):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        s = _get_system(db, sid, owner)
        if not s:
            return _not_found()
        db.delete(s)
        db.commit()
        return {"deleted": sid}
    finally:
        db.close()


@router.post("/design/system/from-project/{pid}")
async def system_from_project(request: Request, pid: str, name: str = Form("")):
    """Infer a reusable design system FROM an existing project's current design(s):
    read each page's HTML, ask the LLM to extract a concise spec, and store it."""
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        p = _get_project(db, pid, owner)
        if not p:
            return _not_found()
        proj_name = p.name
        pages = (db.query(DesignPage).filter(DesignPage.project_id == pid)
                 .order_by(DesignPage.order_index).all())
        htmls = []
        for pg in pages:
            doc = db.query(Document).filter(Document.id == pg.document_id).first() if pg.document_id else None
            c = (doc.current_content if doc else "") or ""
            if c.strip():
                htmls.append(c)
    finally:
        db.close()
    if not htmls:
        return Response(content=json.dumps({"error": "project has no design content to analyze"}),
                        media_type="application/json", status_code=400)
    combined = ("\n\n<!-- ===== next page ===== -->\n\n".join(htmls))[:60000]
    from src.design_service import _design_call
    try:
        raw = await _design_call(
            [{"role": "system", "content": _DESIGN_SYSTEM_EXTRACT_PROMPT},
             {"role": "user", "content": "Extract the design system from this design HTML:\n" + combined}],
            None, owner, max_tokens=1500, temperature=0.2,
        )
        spec = (raw or "").strip()
    except Exception as e:
        logger.warning("system_from_project extraction failed: %s", e)
        return Response(content=json.dumps({"error": str(e)}), media_type="application/json", status_code=500)
    if not spec:
        return Response(content=json.dumps({"error": "extraction produced no spec"}),
                        media_type="application/json", status_code=500)
    db = SessionLocal()
    try:
        sid = _uid()
        sname = (name.strip() or f"{proj_name} system")[:120]
        s = DesignSystem(id=sid, name=sname, owner=owner, spec=spec)
        db.add(s)
        db.commit()
        return {"id": sid, "name": sname, "spec": spec}
    finally:
        db.close()


@router.post("/design/template")
async def create_template(request: Request, name: str = Form(""), kind: str = Form(""),
                          html: str = Form(""), from_page_id: str = Form("")):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        thtml = html or ""
        if from_page_id:
            page = _get_page(db, from_page_id, owner)
            if not page:
                return _not_found()
            doc = db.query(Document).filter(Document.id == page.document_id).first() if page.document_id else None
            thtml = (doc.current_content if doc else "") or ""
        tid = _uid()
        t = DesignTemplate(id=tid, name=(name.strip() or "Template"), owner=owner,
                           kind=(kind.strip() or None), html=thtml)
        db.add(t)
        db.commit()
        return _template_dict(t, full=True)
    finally:
        db.close()


@router.get("/design/templates")
async def list_templates(request: Request, include_archived: bool = False):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        q = db.query(DesignTemplate)
        if owner is not None:
            q = q.filter(DesignTemplate.owner == owner)
        if not include_archived:
            q = q.filter(DesignTemplate.archived == False)  # noqa: E712
        q = q.order_by(DesignTemplate.updated_at.desc())
        return {"templates": [_template_dict(t) for t in q.all()]}
    finally:
        db.close()


@router.get("/design/template/{tid}")
async def get_template(request: Request, tid: str):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        t = _get_template(db, tid, owner)
        if not t:
            return _not_found()
        return _template_dict(t, full=True)
    finally:
        db.close()


@router.delete("/design/template/{tid}")
async def delete_template(request: Request, tid: str):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        t = _get_template(db, tid, owner)
        if not t:
            return _not_found()
        db.delete(t)
        db.commit()
        return {"deleted": tid}
    finally:
        db.close()


@router.post("/design/project/{pid}/page/from-template")
async def page_from_template(request: Request, pid: str, template_id: str = Form(...),
                             title: str = Form("")):
    """Create a page seeded directly from a template's HTML (v1, source=template,
    no LLM call)."""
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    db = SessionLocal()
    try:
        proj = _get_project(db, pid, owner)
        if not proj:
            return _not_found()
        tpl = _get_template(db, template_id, owner)
        if not tpl:
            return _not_found()
        html = tpl.html or ""
        has_content = bool(html.strip())
        n = db.query(DesignPage).filter(DesignPage.project_id == pid).count()
        ptitle = title.strip() or tpl.name or f"Page {n+1}"
        doc_id = _uid()
        db.add(Document(id=doc_id, session_id=None, title=ptitle, language="design",
                        current_content=html, version_count=(1 if has_content else 0),
                        is_active=False, owner=owner))
        if has_content:
            db.add(DocumentVersion(
                id=_uid(), document_id=doc_id, version_number=1, content=html,
                summary=f"Seeded from template: {tpl.name}"[:200], source="template"))
        page = DesignPage(id=_uid(), project_id=pid, document_id=doc_id,
                          title=ptitle, order_index=n)
        db.add(page)
        if not proj.cover_page_id:
            proj.cover_page_id = page.id
        db.commit()
        return {"page_id": page.id}
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Legacy in-editor design endpoint (kept until that surface is removed).
# ---------------------------------------------------------------------------
@router.post("/design")
async def api_design(
    request: Request,
    prompt: str = Form(...),
    session_id: str = Form(""),
    model: str = Form(""),
    doc_id: str = Form(""),
    mode: str = Form("create"),
):
    require_privilege(request, "can_use_design")
    owner = effective_user(request) or None
    ctx = {"session_id": session_id, "owner": owner, "doc_id": doc_id or None}

    is_edit = mode == "edit" and bool(doc_id)
    cur = None
    if is_edit:
        db = SessionLocal()
        try:
            d = db.query(Document).filter(Document.id == doc_id).first()
            if d and owner is not None and d.owner != owner:
                d = None
            cur = d.current_content if d else None
        finally:
            db.close()

    try:
        html = await generate_design_html(prompt, cur, model or None, owner)
    except Exception as e:
        logger.warning("POST /api/design generation failed: %s", e)
        return {"error": str(e)}

    if is_edit:
        return await save_design_version(html, ctx)
    title = ((prompt or "")[:60] or "Design").replace("\n", " ").strip()
    return await save_new_design(html, title, ctx)
