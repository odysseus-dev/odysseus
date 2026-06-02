"""
github_routes.py

REST endpoints for the per-user GitHub integration: save / clear the Personal
Access Token and toggle the master enable flag. The actual GitHub API calls
live in `mcp_servers/github_server.py`; this file just manages the integration
config row in the DB.

Auth model: every endpoint takes the current Odysseus user via `require_user`
(matches the email_routes pattern). Each user owns one GitHub integration row
keyed by username.

PAT storage: encrypted at rest via `src/secret_storage.py`. The plaintext PAT
is only present in memory during a save (to validate against GitHub) and inside
the github MCP subprocess when it builds an auth header. We never echo the PAT
back in API responses — the GET endpoint returns metadata only.
"""

import logging
import re
from datetime import datetime

import httpx
from fastapi import APIRouter, HTTPException, Request
from pydantic import BaseModel, Field

from core.database import SessionLocal, GitHubIntegration
from routes.email_helpers import require_user
from src.secret_storage import encrypt as _encrypt_secret

logger = logging.getLogger(__name__)

GH_API = "https://api.github.com"
GH_TIMEOUT = 15.0
USER_AGENT = "Odysseus-Integration/0.1"

# Default briefing seeded for new users. The agent receives this as a
# system-prompt addendum when GitHub is toggled on for a turn. The universal
# sections ship with real defaults; the taste-specific sections (commit style,
# PR voice) ship as comment-wrapped prompts inviting the user to fill them in.
#
# Comments (<!-- ... -->) are EDITING SCAFFOLD: they guide the user in the
# Settings editor but are stripped by strip_briefing_prompts() before the
# briefing is sent to the agent, so the model never sees the prompts — only
# real standards and whatever the user wrote.
DEFAULT_BRIEFING = """\
You have GitHub tools available. Apply these standards whenever you use them.
This briefing is repo-agnostic: it should hold whether you're contributing to
someone else's open-source project, working on your own fork, or making changes
in an internal codebase. Write for whoever reads your output next (the
maintainer, a teammate, or your future self), and optimize for their attention.

QUALITY BAR
- Write code you'd be willing to defend in review. If you'd cringe explaining a choice to a strong engineer, redo it before submitting.
- Treat first-pass fixes with suspicion: edge case missed, scope creep, or a shape that papers over the bug class instead of removing it? Fix the cause, not the symptom.
- Reader-time is the scarce resource. Every line of diff and every sentence of a PR body is a cost. Earn each one.

HONESTY
- If you didn't run something, don't claim you did. "I tested this" means you executed it and observed the result; "this should work" is fine when stated honestly.
- If you're unsure about an API, path, syntax, or behavior, check the code or docs before relying on it. A confident guess fails silently and burns trust harder than "let me check."
- When the user references an issue, PR, or commit by number, fetch it before assuming what it's about from the title.
- When you report that something changed (a check flipped, an issue closed, a notification cleared), establish the real cause before describing it: fetch the thread or run timeline, don't infer it from earlier conversation.

HOW THE WORK GETS DONE
- Verify the bug or starting condition first, on the actual branch the work will land on. Don't trust that it reproduces, or that it isn't already fixed somewhere you haven't looked.
- Match the codebase's existing idiom. Skim recent merged PRs and a few representative files in the area before drafting; consistency beats personal preference.
- Smallest viable diff. Resist "while I'm here" refactors, test additions, and cleanups; mention them as follow-ups in the PR body instead of smuggling them into the diff.

WRITE ACTIONS (commits, PR comments, opening or editing PRs, pushes)
- Before a write action, say in one or two sentences WHAT you're about to do and WHY. This isn't asking permission (the user opted into write actions in settings); it's giving them a chance to course-correct mid-thread.
- After a write action, state what you did and link to it (PR URL, commit SHA, comment permalink). Don't make the user hunt for the result.

ANTI-PATTERNS
- Don't drop a fix without confirming the bug exists on the current branch.
- Don't make the reader choose between approaches in a comment thread; ship cross-linked alternative PRs instead.
- Don't include unrelated formatting changes in a diff.
- Don't claim work is "tested" without actually running it.
- Don't add scope the user didn't ask for; surface it as a follow-up suggestion instead.

AI-ATTRIBUTION
- Keep the Co-Authored-By trailer on commits you author as an honest disclosure that an agent co-authored the work. Don't add a separate "Generated with..." footer to PR bodies; the trailer is the disclosure, the body should read as the user's own.

<!-- The sections below are yours to fill in. They capture personal house style,
     which no default can guess. Lines wrapped in comment markers like this are
     editing prompts: they are stripped before the briefing is sent to the
     agent, so leave them, replace them, or delete them as you like. -->

COMMIT MESSAGE STYLE
<!-- prompt: How do you like commits written? e.g. Conventional Commits
     (feat:/fix:/chore:), imperative mood, subject under 50 chars, body wrapped
     at 72. Replace this line with your preference, or delete it to leave it open. -->

PR-WRITING VOICE
<!-- prompt: How should the agent sound in PR bodies and review replies? e.g.
     keep reviewer replies to one line ("fixed in <sha>, thanks"); often just
     push the fix instead of replying; no needy closers like "ready for another
     look"; avoid em-dashes. Replace with your own, or delete. -->
"""

# Matches HTML-style comments used as editing scaffold in the briefing.
_BRIEFING_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def strip_briefing_prompts(text: str | None) -> str:
    """Remove the comment-wrapped editing prompts from a briefing so the agent
    sees only real standards + the user's filled-in content. Collapses the
    blank-line runs the removed comments leave behind."""
    if not text:
        return ""
    out = _BRIEFING_COMMENT_RE.sub("", text)
    lines = [ln.rstrip() for ln in out.splitlines()]
    result: list[str] = []
    blanks = 0
    for ln in lines:
        if not ln.strip():
            blanks += 1
            if blanks <= 1:
                result.append("")
        else:
            blanks = 0
            result.append(ln)
    return "\n".join(result).strip() + "\n"


# ── Pydantic request bodies ──

class SavePATRequest(BaseModel):
    pat: str = Field(..., min_length=1, description="GitHub Personal Access Token. Validated against /user before save.")


class UpdateFlagsRequest(BaseModel):
    enabled: bool | None = None


class UpdateBriefingRequest(BaseModel):
    briefing: str = Field(..., description="Markdown briefing text. Empty string resets to the default.")


# ── Helpers ──

def _row_to_dict(row: GitHubIntegration) -> dict:
    """Public view of the integration row. NEVER includes the PAT.

    `briefing` is the RAW text (with editing-prompt comments) for the editor;
    `briefing_agent_preview` is what the agent actually receives after the
    comments are stripped, so the UI can offer a preview."""
    briefing = row.briefing or DEFAULT_BRIEFING
    return {
        "configured": True,
        "github_username": row.github_username,
        "enabled": bool(row.enabled),
        "briefing": briefing,
        "briefing_agent_preview": strip_briefing_prompts(briefing),
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


async def _validate_pat(pat: str) -> dict:
    """Hit GitHub /user with the PAT. Returns the user object on success.
    Raises HTTPException(400) with a useful message on failure so the
    settings UI can surface what went wrong."""
    headers = {
        "Authorization": f"Bearer {pat}",
        "Accept": "application/vnd.github+json",
        "User-Agent": USER_AGENT,
    }
    try:
        async with httpx.AsyncClient(timeout=GH_TIMEOUT) as client:
            resp = await client.get(f"{GH_API}/user", headers=headers)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Could not reach GitHub: {e}")
    if resp.status_code == 401:
        raise HTTPException(status_code=400, detail="GitHub rejected the token (401). Check the PAT value and scopes.")
    if resp.status_code >= 400:
        raise HTTPException(status_code=400, detail=f"GitHub returned {resp.status_code}: {resp.text[:160]}")
    try:
        return resp.json()
    except Exception:
        raise HTTPException(status_code=502, detail="GitHub returned a non-JSON response.")


# ── Router setup ──

def setup_github_routes(mcp_manager=None):
    """Build the router. `mcp_manager` is optional; if passed, we restart the
    github MCP server after a PAT change so its in-process PAT cache flushes.
    Without it, a server restart is needed for new PATs to take effect."""
    router = APIRouter(prefix="/api/github", tags=["github"])

    async def _restart_github_mcp(owner: str):
        if not mcp_manager:
            return
        try:
            # The github MCP server caches the PAT in-process on first use.
            # After a save we want it to re-read from the DB. Easiest path is
            # to disconnect; the next github tool call reconnects.
            if hasattr(mcp_manager, "disconnect_server"):
                await mcp_manager.disconnect_server("github")
        except Exception as e:
            logger.warning(f"github MCP restart after PAT save failed (non-fatal): {e}")

    @router.get("/integration")
    def get_integration(request: Request):
        """Return the current user's integration metadata, or
        {configured: false} if none. Never returns the PAT."""
        owner = require_user(request)
        with SessionLocal() as db:
            row = db.query(GitHubIntegration).filter_by(owner=owner or "").first()
        if not row:
            return {"configured": False, "briefing": DEFAULT_BRIEFING,
                    "briefing_agent_preview": strip_briefing_prompts(DEFAULT_BRIEFING)}
        return _row_to_dict(row)

    @router.post("/integration")
    async def save_integration(request: Request, body: SavePATRequest):
        """Save (or replace) the user's PAT. Validates the token against
        /user first; on success persists encrypted PAT + username. On
        failure returns 400 with GitHub's error message."""
        owner = require_user(request)
        pat = body.pat.strip()
        if not pat:
            raise HTTPException(400, "PAT is empty.")
        user_obj = await _validate_pat(pat)
        gh_username = user_obj.get("login") or "unknown"
        enc = _encrypt_secret(pat)
        with SessionLocal() as db:
            row = db.query(GitHubIntegration).filter_by(owner=owner or "").first()
            if row:
                row.pat_encrypted = enc
                row.github_username = gh_username
                row.enabled = True
                row.updated_at = datetime.utcnow()
            else:
                row = GitHubIntegration(
                    owner=owner or "",
                    pat_encrypted=enc,
                    github_username=gh_username,
                    briefing=DEFAULT_BRIEFING,
                    enabled=True,
                )
                db.add(row)
            db.commit()
            db.refresh(row)
            payload = _row_to_dict(row)
        await _restart_github_mcp(owner or "")
        return payload

    @router.delete("/integration")
    async def delete_integration(request: Request):
        """Remove the integration entirely. PAT, username, everything — gone."""
        owner = require_user(request)
        with SessionLocal() as db:
            row = db.query(GitHubIntegration).filter_by(owner=owner or "").first()
            if row:
                db.delete(row)
                db.commit()
        await _restart_github_mcp(owner or "")
        return {"configured": False}

    @router.post("/integration/flags")
    def update_flags(request: Request, body: UpdateFlagsRequest):
        """Update the master enabled toggle."""
        owner = require_user(request)
        with SessionLocal() as db:
            row = db.query(GitHubIntegration).filter_by(owner=owner or "").first()
            if not row:
                raise HTTPException(404, "GitHub integration not configured.")
            if body.enabled is not None:
                row.enabled = bool(body.enabled)
            row.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(row)
            return _row_to_dict(row)

    @router.post("/integration/briefing")
    def update_briefing(request: Request, body: UpdateBriefingRequest):
        """Update the agent briefing text. Empty string resets to the default.
        Stored RAW (with editing-prompt comments); the comments are stripped
        only when the briefing is injected into the agent."""
        owner = require_user(request)
        with SessionLocal() as db:
            row = db.query(GitHubIntegration).filter_by(owner=owner or "").first()
            if not row:
                raise HTTPException(404, "GitHub integration not configured.")
            row.briefing = body.briefing.strip() or DEFAULT_BRIEFING
            row.updated_at = datetime.utcnow()
            db.commit()
            db.refresh(row)
            return _row_to_dict(row)

    return router
