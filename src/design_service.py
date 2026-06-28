"""design_service.py — Odysseus Design generation core.

Single source of truth for turning a natural-language prompt into a
self-contained HTML design document. Shared by:
  - the chat tools (src/agent_tools/design_tools.py), and
  - the panel route (routes/design_routes.py).

Generation is a plain text LLM call (no tools) via the existing
endpoint-resolver + fallback machinery, so it works with any configured
OpenAI-compatible / Claude-subscription / local endpoint.
"""

import logging
import re
from typing import Optional

logger = logging.getLogger(__name__)

# The "taste" layer — a senior-designer rubric injected into every phase so the
# brief, the build and the critique all share the same bar. Ported from how a
# strong design agent is briefed (anti-slop, commit to a direction, real scale).
_DESIGN_RUBRIC = """DESIGN PRINCIPLES (non-negotiable):
- Embody the right expert for the MEDIUM (landing page, dashboard, marketing site, slide, app screen, email, prototype). Avoid generic web-design tropes unless it is literally a web page.
- Commit to ONE bold, specific aesthetic direction — a real point of view on type, color, layout and motion. Generic and safe = failure.
- Kill "AI slop": NO rainbow/aggressive gradient backgrounds; NO emoji as icons (use clean inline SVG); NO "card with rounded corners + left-border accent stripe"; do NOT hand-draw complex illustrations, logos or photos as SVG — use https://placehold.co/ placeholders; AVOID overused fonts (Inter, Roboto, Arial, system-ui, Fraunces) — choose a deliberate, characterful Google Fonts pairing.
- Real typographic scale: confident, oversized headings; clear hierarchy; generous whitespace; a consistent spacing rhythm; AA contrast. Use text-wrap: balance/pretty.
- Less is more: every element earns its place. No filler copy, no decorative stat/number/icon slop.
- Color: use the user's brand if provided; otherwise a harmonious palette defined with oklch. Don't invent clashing colors.
- Modern, responsive (320 -> 1440), semantic HTML, visible focus, keyboard-navigable.
- A tasteful placeholder beats a bad attempt at the real thing."""

# Strict single-file output contract — reused for the build + edit phases.
_DESIGN_OUTPUT_CONTRACT = """OUTPUT CONTRACT (strict):
- Return ONLY the HTML. No markdown fences, no commentary. Start at <!DOCTYPE html>.
- ONE self-contained file: inline <style> in <head>, JS in one <script> at the end of <body>.
- Allowed external resources ONLY: Tailwind Play CDN (<script src="https://cdn.tailwindcss.com"></script>) and Google Fonts (<link>). Nothing else — no fetch/XHR/WebSocket/analytics/tracking.
- No localStorage/sessionStorage/cookies — the preview sandbox blocks them and they throw.
- Images: inline SVG icons, https://placehold.co/ placeholders, or data: URIs. Never hotlink real photos.

TWEAKS PANEL (always include):
- Add a small floating "Tweaks" panel (fixed bottom-right, hidden by default) that lets the user live-toggle key design choices via CSS variables — accent color, font pairing, density/spacing, and ONE layout/style variant.
- Toggle it on this message from the parent frame, and start hidden:
  window.addEventListener('message', function(e){ if(e.data && e.data.type==='__dm_tweaks'){ /* show/hide your panel using e.data.show */ } });
- For the accent color control, use a small inline ROW OF PRESET SWATCHES (buttons) plus, optionally, a hex `<input type="text">`. Do NOT use a native `<input type="color">` — its OS picker opens detached at the page corner inside the scaled preview. Keep the whole panel and its controls INSIDE the panel; never rely on a native popup.
- Keep it small and tasteful; apply changes live."""

DESIGN_SYSTEM_PROMPT = """You are Odysseus Design — a senior product/visual designer who outputs ONE self-contained HTML document that renders a polished, production-quality design.

""" + _DESIGN_RUBRIC + """

""" + _DESIGN_OUTPUT_CONTRACT + """

WHEN EDITING (a current design is provided): apply the requested change(s) precisely, preserve everything else, and return the FULL updated document.

Write visible copy in the user's language."""

# Phase prompts for the agentic flow.
DESIGN_BRIEF_PROMPT = """You are the lead designer scoping a design before building it.

""" + _DESIGN_RUBRIC + """

Given the user's request (and any clarifying answers), commit to an opinionated direction and a concrete build plan.
Return STRICT JSON only — no prose, no fences:
{
  "brief": "2-4 sentences: the aesthetic direction, mood and layout approach you commit to. Specific and opinionated, not generic.",
  "system": {"type": "specific Google Fonts pairing", "color": "palette direction", "voice": "copy tone"},
  "todos": ["6-9 short, concrete, ordered build tasks — e.g. 'Hero: oversized serif headline + single CTA, asymmetric'"]
}"""

DESIGN_CRITIQUE_PROMPT = """You are a demanding design director reviewing an HTML design against the rubric.

""" + _DESIGN_RUBRIC + """

Call out ONLY real, high-impact problems (slop, weak hierarchy, poor contrast, cramped spacing, generic/overused type, filler content, broken responsive, emoji-as-icons, gradient soup). Ignore nitpicks. If it's genuinely strong, ship it.
Return STRICT JSON only:
{"verdict": "ship" | "revise", "issues": ["concrete, actionable fix", ...]}"""

DESIGN_CLARIFY_PROMPT = """You decide whether to ask the user clarifying questions before designing.
Strongly prefer to PROCEED with confident, opinionated choices. Ask ONLY when the request is genuinely ambiguous in ways that would materially change the design (audience/purpose, brand identity, the key content/sections, or platform).
Return STRICT JSON only:
{"questions": []}                                          // when specific enough — this is the common case
or
{"questions": [{"id":"audience","q":"Who is the primary audience?","options":["Investors","End users","Developers"]}, ...up to 3]}
Keep each question short; include 2-4 quick options when sensible (the user can also type their own)."""

# Fallbacks used when settings/owner overrides are absent.
_DEFAULT_DESIGN_MAX_TOKENS = 24000
_DESIGN_TEMPERATURE = 0.5
_DESIGN_TIMEOUT = 300


def _resolve_design_candidates(model: Optional[str], owner: Optional[str]) -> list:
    """Build the (url, model, headers) candidate chain for design generation.

    `model` may arrive as 'modelId@endpoint_id' (from the panel dropdown),
    a bare 'modelId', or None. Resolution order for the primary:
      explicit model/endpoint -> design_* settings -> default_* settings.
    Vision fallbacks are appended as a safety net (design is vision-friendly).
    """
    from src.settings import get_user_setting, load_settings
    from src.endpoint_resolver import (
        resolve_endpoint_by_id,
        resolve_vision_fallback_candidates,
    )

    settings = load_settings()
    o = owner or ""
    ep_id: Optional[str] = None
    mid: Optional[str] = None
    if model and "@" in model:
        mid, ep_id = model.split("@", 1)
    elif model:
        mid = model

    ep_id = (ep_id or "").strip() or \
        (get_user_setting("design_endpoint_id", o, settings.get("design_endpoint_id", "")) or "").strip() or \
        (get_user_setting("default_endpoint_id", o, settings.get("default_endpoint_id", "")) or "").strip()
    mid = (mid or "").strip() or \
        (get_user_setting("design_model", o, settings.get("design_model", "")) or "").strip() or \
        (get_user_setting("default_model", o, settings.get("default_model", "")) or "").strip()

    candidates: list = []
    primary = resolve_endpoint_by_id(ep_id, mid, owner=owner)
    if primary:
        candidates.append(primary)
    try:
        candidates += resolve_vision_fallback_candidates(owner=owner)
    except Exception:
        logger.debug("design: vision fallback resolution failed", exc_info=True)
    return candidates


def _extract_html(raw: str) -> str:
    """Strip markdown fences / preamble and return the HTML document."""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:html)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    s = s.strip()
    low = s.lower()
    i = low.find("<!doctype")
    if i == -1:
        i = low.find("<html")
    s = s[i:].strip() if i != -1 else s
    # Drop any trailing junk (stray fence/commentary) after the document end.
    j = s.lower().rfind("</html>")
    if j != -1:
        s = s[: j + len("</html>")]
    return s


def _parse_json_block(raw: str) -> dict:
    """Best-effort parse of a JSON object from an LLM reply (tolerates fences
    and surrounding prose)."""
    import json
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*", "", s)
    s = re.sub(r"\s*```$", "", s)
    a, b = s.find("{"), s.rfind("}")
    if a != -1 and b > a:
        s = s[a:b + 1]
    return json.loads(s)


async def _design_call(messages: list, model, owner, max_tokens: int,
                       temperature: float = _DESIGN_TEMPERATURE) -> str:
    """Resolve endpoints and run one design LLM call (shared by all phases)."""
    from src.llm_core import llm_call_async_with_fallback
    candidates = _resolve_design_candidates(model, owner)
    if not candidates:
        raise RuntimeError("Nenhum endpoint de design configurado")
    return await llm_call_async_with_fallback(
        candidates, messages=messages, temperature=temperature,
        max_tokens=max_tokens, timeout=_DESIGN_TIMEOUT,
    )


async def clarify_design(prompt: str, model=None, owner=None) -> list:
    """Return up to 3 clarifying questions for a vague request, or [] to proceed.
    Never raises for the caller — returns [] on any failure (proceed)."""
    prompt = (prompt or "").strip()
    if not prompt:
        return []
    try:
        raw = await _design_call(
            [{"role": "system", "content": DESIGN_CLARIFY_PROMPT},
             {"role": "user", "content": f"Design request:\n{prompt}"}],
            model, owner, max_tokens=600, temperature=0.3,
        )
        qs = (_parse_json_block(raw) or {}).get("questions") or []
        # Normalize + cap.
        out = []
        for q in qs[:3]:
            if isinstance(q, dict) and (q.get("q") or q.get("question")):
                out.append({
                    "id": str(q.get("id") or len(out)),
                    "q": q.get("q") or q.get("question"),
                    "options": [str(o) for o in (q.get("options") or [])][:4],
                })
        return out
    except Exception as e:
        logger.info("clarify_design skipped: %s", e)
        return []


def _design_system_clause(design_system: Optional[str]) -> str:
    """Format an authoritative brand-guidance block from a DesignSystem spec.
    Overrides the rubric's "choose a pairing/palette" — use the system's instead."""
    spec = (design_system or "").strip()
    if not spec:
        return ""
    return (
        "FOLLOW THIS DESIGN SYSTEM exactly — it is authoritative and OVERRIDES the "
        "generic rubric's defaults for fonts, color palette, components and voice "
        "(do not 'choose a pairing'; use the system's):\n" + spec
    )


def _reference_clause(reference_text: Optional[str]) -> str:
    """Label the user's attached reference material (pasted text + fetched
    file/URL content) as context for the design. Empty -> ''."""
    rt = (reference_text or "").strip()
    if not rt:
        return ""
    return (
        "REFERENCE CONTEXT the user attached — use it to inform the design "
        "(brand, content, structure):\n" + rt
    )


def _user_content(text: str, images: Optional[list]):
    """Build an OpenAI-style message ``content``: a plain string when there are
    no images, or a ``[text, image_url...]`` block list for vision. Only data:
    URLs are accepted and the list is capped at 4 images (non-vision models skip
    images gracefully — see src/llm_core.py)."""
    imgs = [u for u in (images or [])
            if isinstance(u, str) and u.startswith("data:")][:4]
    if not imgs:
        return text
    blocks = [{"type": "text", "text": text}]
    for u in imgs:
        blocks.append({"type": "image_url", "image_url": {"url": u}})
    return blocks


async def plan_design(prompt: str, model=None, owner=None, answers: str = "",
                      design_system: Optional[str] = None,
                      reference_text: str = "", images: Optional[list] = None) -> dict:
    """Produce the design brief + build todo list. Falls back to a minimal plan
    so the build can always proceed. When ``design_system`` is given, the brief
    must commit to that brand's fonts/palette/voice instead of inventing one."""
    user = f"Design request:\n{(prompt or '').strip()}"
    if answers:
        user += f"\n\nClarifying answers:\n{answers.strip()}"
    _ds = _design_system_clause(design_system)
    if _ds:
        user += "\n\n" + _ds
    _ref = _reference_clause(reference_text)
    if _ref:
        user += "\n\n" + _ref
    try:
        raw = await _design_call(
            [{"role": "system", "content": DESIGN_BRIEF_PROMPT},
             {"role": "user", "content": _user_content(user, images)}],
            model, owner, max_tokens=1500, temperature=0.6,
        )
        data = _parse_json_block(raw) or {}
    except Exception as e:
        logger.info("plan_design fallback (%s)", e)
        data = {}
    brief = (data.get("brief") or "").strip()
    todos = [str(t).strip() for t in (data.get("todos") or []) if str(t).strip()][:9]
    system = data.get("system") if isinstance(data.get("system"), dict) else {}
    if not todos:
        todos = ["Definir direção visual e tipografia", "Layout e hierarquia",
                 "Seções principais de conteúdo", "Estados interativos e responsividade",
                 "Polir espaçamento, cor e detalhes"]
    return {"brief": brief, "todos": todos, "system": system}


async def critique_design(html: str, model=None, owner=None) -> dict:
    """Critique a generated design against the rubric. Returns
    {verdict: ship|revise, issues: [...]}. On failure, ships (no-op)."""
    try:
        raw = await _design_call(
            [{"role": "system", "content": DESIGN_CRITIQUE_PROMPT},
             {"role": "user", "content": f"Review this design HTML:\n{html[:60000]}"}],
            model, owner, max_tokens=1200, temperature=0.2,
        )
        data = _parse_json_block(raw) or {}
        issues = [str(i).strip() for i in (data.get("issues") or []) if str(i).strip()][:8]
        verdict = "revise" if (data.get("verdict") == "revise" and issues) else "ship"
        return {"verdict": verdict, "issues": issues}
    except Exception as e:
        logger.info("critique_design skipped: %s", e)
        return {"verdict": "ship", "issues": []}


async def generate_design_html(
    prompt: str,
    current_html: Optional[str] = None,
    model: Optional[str] = None,
    owner: Optional[str] = None,
    brief: Optional[dict] = None,
    design_system: Optional[str] = None,
    reference_text: str = "",
    images: Optional[list] = None,
) -> str:
    """Generate (or edit) a self-contained HTML design from a prompt.

    When `brief` (from plan_design) is given on a create, the agent's committed
    direction + todo list are folded into the build instruction.

    When `design_system` (a brand/style spec) is given, it is injected as
    authoritative brand guidance that overrides the generic rubric's font/color
    choices on both create and edit.

    Raises RuntimeError if no endpoint resolves or the model returns no HTML.
    """
    from src.llm_core import llm_call_async_with_fallback
    from src.settings import get_user_setting, load_settings

    prompt = (prompt or "").strip()
    if not prompt:
        raise RuntimeError("Prompt de design vazio")

    settings = load_settings()
    o = owner or ""
    try:
        max_tokens = int(get_user_setting("design_max_tokens", o, settings.get("design_max_tokens", _DEFAULT_DESIGN_MAX_TOKENS)) or _DEFAULT_DESIGN_MAX_TOKENS)
    except (TypeError, ValueError):
        max_tokens = _DEFAULT_DESIGN_MAX_TOKENS
    sys_prompt = (get_user_setting("design_system_prompt", o, settings.get("design_system_prompt", "")) or "").strip() or DESIGN_SYSTEM_PROMPT

    candidates = _resolve_design_candidates(model, owner)
    if not candidates:
        raise RuntimeError("Nenhum endpoint de design configurado")

    if current_html:
        user_msg = (
            "Edit the following design. Apply ONLY this change and return the FULL "
            "updated HTML document.\n\nCHANGE REQUESTED:\n"
            f"{prompt}\n\nCURRENT DESIGN HTML:\n{current_html}"
        )
    else:
        user_msg = f"Create a design for this request:\n{prompt}"
        if brief:
            _b = (brief.get("brief") or "").strip()
            _sys = brief.get("system") or {}
            _todos = brief.get("todos") or []
            parts = []
            if _b:
                parts.append(f"COMMITTED DIRECTION:\n{_b}")
            if _sys:
                parts.append("DESIGN SYSTEM:\n" + "\n".join(
                    f"- {k}: {v}" for k, v in _sys.items() if v))
            if _todos:
                parts.append("BUILD PLAN (implement every item):\n" + "\n".join(
                    f"- {t}" for t in _todos))
            if parts:
                user_msg += "\n\n" + "\n\n".join(parts) + (
                    "\n\nBuild the complete design now, honoring the direction and plan above.")

    _ds = _design_system_clause(design_system)
    if _ds:
        # Authoritative brand guidance — overrides the rubric's generic choices.
        sys_prompt = sys_prompt + "\n\n" + _ds

    _ref = _reference_clause(reference_text)
    if _ref:
        user_msg += "\n\n" + _ref

    messages = [
        {"role": "system", "content": sys_prompt},
        {"role": "user", "content": _user_content(user_msg, images)},
    ]

    raw = await llm_call_async_with_fallback(
        candidates,
        messages=messages,
        temperature=_DESIGN_TEMPERATURE,
        max_tokens=max_tokens,
        timeout=_DESIGN_TIMEOUT,
    )

    html = _extract_html(raw)
    if "<" not in html:
        raise RuntimeError("O modelo não retornou HTML válido")
    # Truncation guard: a generation cut off by max_tokens would persist a broken
    # design over the working one. Require a plausible document end + size.
    low = html.lower()
    if "</html>" not in low and "</body>" not in low:
        raise RuntimeError("A saída do modelo parece truncada (sem </body>/</html>); design não aplicado")
    if current_html and len(html) < 0.5 * len(current_html):
        raise RuntimeError("A saída ficou suspeitamente menor que o design atual; não aplicada")
    return html


# ---------------------------------------------------------------------------
# Storage helpers — write design HTML straight into the existing Document /
# DocumentVersion tables (language="design"). We do NOT route through
# CreateDocumentTool because its <title>/<content> XML extraction would
# mis-parse the generated HTML (which contains a <title> in its <head>).
# ---------------------------------------------------------------------------
async def save_new_design(html: str, title: Optional[str], ctx: dict) -> dict:
    import uuid
    from src.database import SessionLocal, Document, DocumentVersion, Session as DbSession
    from src.agent_tools.document_tools import set_active_document

    session_id = ctx.get("session_id")
    owner = ctx.get("owner")
    if not session_id:
        return {"error": "No session context for design creation"}

    db = SessionLocal()
    try:
        doc_id = str(uuid.uuid4())
        ver_id = str(uuid.uuid4())
        _sess = db.query(DbSession).filter(DbSession.id == session_id).first()
        if owner is not None and (not _sess or _sess.owner != owner):
            return {"error": "Cannot create design in another user's session"}
        _owner = _sess.owner if _sess else None
        doc = Document(
            id=doc_id,
            session_id=session_id,
            title=(title or "Design"),
            language="design",
            current_content=html,
            version_count=1,
            is_active=True,
            owner=_owner,
        )
        ver = DocumentVersion(
            id=ver_id,
            document_id=doc_id,
            version_number=1,
            content=html,
            summary="Created by Odysseus Design",
            source="ai",
        )
        db.add(doc)
        db.add(ver)
        db.commit()
        set_active_document(doc_id)
        try:
            from src.event_bus import fire_event
            fire_event("document_created", _owner)
        except Exception:
            logger.debug("document_created event dispatch failed", exc_info=True)
        return {
            "action": "create",
            "doc_id": doc_id,
            "title": (title or "Design"),
            "language": "design",
            "content": html,
            "version": 1,
        }
    except Exception as e:
        db.rollback()
        return {"error": f"Failed to create design: {e}"}
    finally:
        db.close()


async def create_design_project(prompt: str, title: Optional[str], model: Optional[str], ctx: dict) -> dict:
    """Chat entry point: generate a design and store it as a DesignProject +
    DesignPage (backed by a Document) so it opens in the dedicated Design Maker
    surface instead of the document editor. Returns ids for the design_open
    SSE event."""
    import uuid
    from core.database import (
        SessionLocal, Document, DocumentVersion, DesignProject, DesignPage,
    )

    owner = ctx.get("owner")
    session_id = ctx.get("session_id") or None

    # Generate first so a failure leaves no orphan project/page.
    html = await generate_design_html(prompt, None, model, owner)

    db = SessionLocal()
    try:
        name = (title or (prompt or "")[:60] or "Untitled design").replace("\n", " ").strip()
        pid = str(uuid.uuid4())
        doc_id = str(uuid.uuid4())
        page_id = str(uuid.uuid4())
        db.add(DesignProject(id=pid, name=name, owner=owner, session_id=session_id,
                             cover_page_id=page_id))
        db.add(Document(id=doc_id, session_id=None, title=name, language="design",
                        current_content=html, version_count=1, is_active=False, owner=owner))
        db.add(DocumentVersion(id=str(uuid.uuid4()), document_id=doc_id, version_number=1,
                               content=html, summary="Created by Design Maker (chat)", source="ai"))
        db.add(DesignPage(id=page_id, project_id=pid, document_id=doc_id, title=name, order_index=0))
        db.commit()
        # Pin the page's doc as active so a follow-up edit_design targets it.
        try:
            from src.agent_tools.document_tools import set_active_document
            set_active_document(doc_id)
        except Exception:
            pass
        return {
            "design_open": {"project_id": pid, "page_id": page_id, "title": name},
            "summary": f'Design "{name}" criado e aberto no Design Maker.',
        }
    except Exception as e:
        db.rollback()
        return {"error": f"Failed to create design project: {e}"}
    finally:
        db.close()


async def save_design_version(html: str, ctx: dict) -> dict:
    import uuid
    from src.database import SessionLocal, Document, DocumentVersion
    from src.agent_tools.document_tools import set_active_document
    import src.agent_tools.document_tools as _dt

    owner = ctx.get("owner")
    target_id = ctx.get("doc_id") or _dt._active_document_id
    db = SessionLocal()
    try:
        doc = None
        if target_id:
            doc = db.query(Document).filter(Document.id == target_id).first()
            if doc and owner is not None and doc.owner != owner:
                doc = None
        if not doc:
            return {"error": "No design document to update"}
        new_ver = doc.version_count + 1
        ver = DocumentVersion(
            id=str(uuid.uuid4()),
            document_id=doc.id,
            version_number=new_ver,
            content=html,
            summary="Edited by Odysseus Design",
            source="ai",
        )
        doc.current_content = html
        doc.version_count = new_ver
        db.add(ver)
        db.commit()
        set_active_document(doc.id)
        return {
            "action": "update",
            "doc_id": doc.id,
            "title": doc.title,
            "language": doc.language,
            "content": html,
            "version": new_ver,
        }
    except Exception as e:
        db.rollback()
        return {"error": f"Failed to update design: {e}"}
    finally:
        db.close()
