"""Engineering Missions API.

The first mission type is an auditable GitHub PR review: fetch PR metadata,
run a local Go diff analyzer, optionally ask the configured utility model for
review synthesis, and store the final report as a reusable portfolio artifact.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import shutil
import subprocess
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from pydantic import BaseModel, Field

from core.constants import BASE_DIR
from core.database import EngineeringMission, SessionLocal, utcnow_naive
from src.auth_helpers import effective_user, require_user


router = APIRouter(prefix="/api/engineering-missions", tags=["engineering-missions"])

GO_ANALYZER_DIR = os.path.join(BASE_DIR, "tools", "github-diff-analyzer")

_PR_URL_RE = re.compile(
    r"^https?://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)/pull/(?P<number>\d+)(?:[/?#].*)?$"
)


class PRReviewRequest(BaseModel):
    pr_url: str = Field(..., min_length=12, max_length=500)
    include_ai: bool = True


def _public_url(request: Optional[Request], share_token: Optional[str]) -> Optional[str]:
    if not request or not share_token:
        return None
    return f"{str(request.base_url).rstrip('/')}/engineering/reports/{share_token}"


def _report_filename(mission: EngineeringMission, ext: str) -> str:
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", mission.title or mission.id).strip("-").lower()
    return f"{base or 'engineering-mission'}-{mission.id[:8]}.{ext.lstrip('.')}"


def _mission_to_dict(mission: EngineeringMission, request: Optional[Request] = None, include_share_token: bool = True) -> Dict[str, Any]:
    share_token = mission.share_token if include_share_token else None
    return {
        "id": mission.id,
        "owner": mission.owner,
        "kind": mission.kind,
        "status": mission.status,
        "target_url": mission.target_url,
        "title": mission.title,
        "summary": mission.summary,
        "report_markdown": mission.report_markdown,
        "payload": mission.payload or {},
        "audit_log": mission.audit_log or [],
        "public_report": bool(mission.public_report),
        "share_token": share_token if mission.public_report else None,
        "public_url": _public_url(request, mission.share_token) if mission.public_report else None,
        "error": mission.error,
        "created_at": mission.created_at.isoformat() if mission.created_at else None,
        "updated_at": mission.updated_at.isoformat() if mission.updated_at else None,
        "finished_at": mission.finished_at.isoformat() if mission.finished_at else None,
        "published_at": mission.published_at.isoformat() if mission.published_at else None,
    }


def _public_mission_to_dict(mission: EngineeringMission, request: Optional[Request] = None) -> Dict[str, Any]:
    data = _mission_to_dict(mission, request=request, include_share_token=False)
    data.pop("owner", None)
    data.pop("share_token", None)
    return data


def _mission_export_payload(mission: EngineeringMission, request: Optional[Request] = None) -> Dict[str, Any]:
    data = _mission_to_dict(mission, request=request)
    return {
        "exported_at": utcnow_naive().isoformat(),
        "mission": data,
        "report_markdown": mission.report_markdown or "",
    }


def _markdown_export_response(mission: EngineeringMission) -> Response:
    filename = _report_filename(mission, "md")
    return Response(
        content=mission.report_markdown or "",
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _json_export_response(mission: EngineeringMission, request: Optional[Request] = None) -> Response:
    filename = _report_filename(mission, "json")
    return Response(
        content=json.dumps(_mission_export_payload(mission, request=request), ensure_ascii=False, indent=2, default=str),
        media_type="application/json; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


def _parse_pr_url(url: str) -> Tuple[str, str, int]:
    match = _PR_URL_RE.match((url or "").strip())
    if not match:
        raise HTTPException(400, "Paste a GitHub pull request URL like https://github.com/owner/repo/pull/123")
    return match.group("owner"), match.group("repo"), int(match.group("number"))


def _github_headers() -> Dict[str, str]:
    headers = {
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "Odysseus-Engineering-Missions",
    }
    token = (os.getenv("GITHUB_TOKEN") or os.getenv("GH_TOKEN") or "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


def _step(stage: str, title: str, status: str = "completed", detail: str = "", meta: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    return {
        "stage": stage,
        "title": title,
        "status": status,
        "detail": detail,
        "meta": meta or {},
        "at": utcnow_naive().isoformat(),
    }


def _save_step(db, mission: EngineeringMission, step: Dict[str, Any]) -> None:
    mission.audit_log = [*(mission.audit_log or []), step]
    mission.updated_at = utcnow_naive()
    db.add(mission)
    db.commit()
    db.refresh(mission)


async def _fetch_github_pr(owner: str, repo: str, number: int) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    base = f"https://api.github.com/repos/{owner}/{repo}"
    headers = _github_headers()
    async with httpx.AsyncClient(headers=headers, timeout=25, follow_redirects=True) as client:
        pr_resp = await client.get(f"{base}/pulls/{number}")
        if pr_resp.status_code == 404:
            raise HTTPException(404, "GitHub PR not found or not visible without a token")
        if pr_resp.status_code in (401, 403):
            raise HTTPException(pr_resp.status_code, "GitHub API rejected the request. Add GITHUB_TOKEN for private repos or higher rate limits.")
        pr_resp.raise_for_status()
        pr = pr_resp.json()

        files: List[Dict[str, Any]] = []
        page = 1
        while page <= 4:
            files_resp = await client.get(f"{base}/pulls/{number}/files", params={"per_page": 100, "page": page})
            files_resp.raise_for_status()
            batch = files_resp.json()
            files.extend(batch)
            if len(batch) < 100:
                break
            page += 1
    return pr, files


def _fallback_analysis(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    total_additions = sum(int(f.get("additions") or 0) for f in files)
    total_deletions = sum(int(f.get("deletions") or 0) for f in files)
    test_files = [f for f in files if re.search(r"(^|/)(test|tests|spec|__tests__)/|(_test|\.test|\.spec)\.", f.get("filename", ""), re.I)]
    risk_signals = []
    dependency_files = []
    for f in files:
        name = f.get("filename", "")
        if re.search(r"(^|/)(package-lock\.json|package\.json|requirements.*\.txt|pyproject\.toml|go\.mod|go\.sum|Cargo\.toml|Dockerfile|docker-compose.*\.yml)$", name):
            dependency_files.append(name)
        if int(f.get("changes") or 0) > 400:
            risk_signals.append({"severity": "medium", "label": "Large file delta", "detail": f"{name} changes {f.get('changes')} lines", "file": name})
        patch = f.get("patch") or ""
        if re.search(r"\b(eval|exec|subprocess|shell=True|innerHTML)\b", patch):
            risk_signals.append({"severity": "high", "label": "Sensitive API touched", "detail": f"{name} mentions execution or HTML injection APIs", "file": name})
    if dependency_files:
        risk_signals.append({"severity": "medium", "label": "Dependency or runtime surface changed", "detail": ", ".join(dependency_files[:5]), "file": dependency_files[0]})
    if files and not test_files:
        risk_signals.append({"severity": "high", "label": "No test files changed", "detail": "The PR does not appear to update tests.", "file": ""})
    risk_score = min(100, len(risk_signals) * 18 + max(0, len(files) - 6) * 3 + (total_additions + total_deletions) // 90)
    return {
        "engine": "python-fallback",
        "risk_score": risk_score,
        "risk_level": "high" if risk_score >= 65 else "medium" if risk_score >= 35 else "low",
        "totals": {"files": len(files), "additions": total_additions, "deletions": total_deletions, "test_files": len(test_files)},
        "languages": {},
        "risk_signals": risk_signals,
        "file_breakdown": [
            {"filename": f.get("filename"), "status": f.get("status"), "additions": f.get("additions"), "deletions": f.get("deletions"), "changes": f.get("changes")}
            for f in files[:40]
        ],
        "recommendations": [
            "Ask for explicit test evidence before approving.",
            "Review touched dependency/runtime files manually.",
            "Run the changed package's focused test suite locally.",
        ],
    }


def _run_diff_analyzer(files: List[Dict[str, Any]]) -> Dict[str, Any]:
    payload = json.dumps({"files": files}, ensure_ascii=False)
    go = shutil.which("go")
    if not go:
        analysis = _fallback_analysis(files)
        analysis["engine_note"] = "Go was not found on PATH, so Python fallback analysis ran."
        return analysis
    try:
        result = subprocess.run(
            [go, "run", "."],
            cwd=GO_ANALYZER_DIR,
            input=payload,
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if result.returncode != 0:
            analysis = _fallback_analysis(files)
            analysis["engine_note"] = (result.stderr or result.stdout or "Go analyzer failed").strip()[:500]
            return analysis
        return json.loads(result.stdout)
    except Exception as exc:
        analysis = _fallback_analysis(files)
        analysis["engine_note"] = f"Go analyzer unavailable: {exc}"
        return analysis


def _small_pr_payload(pr: Dict[str, Any], files: List[Dict[str, Any]]) -> Dict[str, Any]:
    return {
        "number": pr.get("number"),
        "title": pr.get("title"),
        "state": pr.get("state"),
        "draft": bool(pr.get("draft")),
        "url": pr.get("html_url"),
        "author": (pr.get("user") or {}).get("login"),
        "base": f"{((pr.get('base') or {}).get('repo') or {}).get('full_name', '')}@{(pr.get('base') or {}).get('ref', '')}",
        "head": f"{((pr.get('head') or {}).get('repo') or {}).get('full_name', '')}@{(pr.get('head') or {}).get('ref', '')}",
        "additions": pr.get("additions"),
        "deletions": pr.get("deletions"),
        "changed_files": pr.get("changed_files") or len(files),
        "files": [
            {
                "filename": f.get("filename"),
                "status": f.get("status"),
                "additions": f.get("additions"),
                "deletions": f.get("deletions"),
                "changes": f.get("changes"),
            }
            for f in files[:100]
        ],
    }


async def _maybe_ai_review(owner: str, pr_payload: Dict[str, Any], analysis: Dict[str, Any]) -> Optional[str]:
    try:
        from src.endpoint_resolver import resolve_endpoint
        from src.llm_core import llm_call_async
    except Exception:
        return None
    url, model, headers = resolve_endpoint("utility", owner=owner)
    if not url or not model:
        return None

    compact = {
        "pr": pr_payload,
        "analysis": {
            "risk_score": analysis.get("risk_score"),
            "risk_level": analysis.get("risk_level"),
            "totals": analysis.get("totals"),
            "languages": analysis.get("languages"),
            "risk_signals": (analysis.get("risk_signals") or [])[:12],
            "recommendations": (analysis.get("recommendations") or [])[:8],
        },
    }
    prompt = (
        "Review this GitHub pull request as a senior fullstack engineer. "
        "Be concrete, risk-oriented, and concise. Return exactly these sections: "
        "Executive read, Highest-risk files, Missing test evidence, Suggested review comments, Merge recommendation.\n\n"
        + json.dumps(compact, ensure_ascii=False)[:18000]
    )
    try:
        return await llm_call_async(
            url=url,
            model=model,
            headers=headers,
            messages=[
                {"role": "system", "content": "You are a careful senior code reviewer. Never invent files or tests not present in the input."},
                {"role": "user", "content": prompt},
            ],
            temperature=0.2,
            max_tokens=1400,
            timeout=35,
        )
    except Exception:
        return None


def _render_file_table(files: List[Dict[str, Any]], limit: int = 18) -> str:
    rows = ["| File | Status | + | - |", "|---|---:|---:|---:|"]
    for f in files[:limit]:
        rows.append(f"| `{f.get('filename', '')}` | {f.get('status', '')} | {f.get('additions', 0)} | {f.get('deletions', 0)} |")
    if len(files) > limit:
        rows.append(f"| ...and {len(files) - limit} more |  |  |  |")
    return "\n".join(rows)


def _build_report(pr: Dict[str, Any], files: List[Dict[str, Any]], analysis: Dict[str, Any], ai_review: Optional[str], audit: List[Dict[str, Any]]) -> Tuple[str, str]:
    title = pr.get("title") or "GitHub PR Review"
    repo = ((pr.get("base") or {}).get("repo") or {}).get("full_name", "")
    author = (pr.get("user") or {}).get("login", "unknown")
    totals = analysis.get("totals") or {}
    signals = analysis.get("risk_signals") or []
    recs = analysis.get("recommendations") or []
    risk_level = analysis.get("risk_level", "unknown")
    risk_score = analysis.get("risk_score", 0)
    summary = f"{repo}#{pr.get('number')} is {risk_level} risk ({risk_score}/100): {len(files)} files, {totals.get('additions', 0)} additions, {totals.get('deletions', 0)} deletions."

    signal_lines = "\n".join(
        f"- **{s.get('severity', 'info').title()}**: {s.get('label', 'Signal')} - {s.get('detail', '')}"
        + (f" (`{s.get('file')}`)" if s.get("file") else "")
        for s in signals[:12]
    ) or "- No major deterministic risk signals found."
    rec_lines = "\n".join(f"- {r}" for r in recs[:8]) or "- Run the relevant test suite and inspect the changed paths manually."
    langs = analysis.get("languages") or {}
    lang_lines = ", ".join(f"{k}: {v}" for k, v in sorted(langs.items(), key=lambda kv: (-kv[1], kv[0]))[:8]) or "No language breakdown available"
    audit_lines = "\n".join(f"- {a.get('at', '')}: {a.get('title')} ({a.get('status')})" for a in audit)

    report = f"""# Engineering Mission: PR Review

## Pull Request
- **Title:** {title}
- **Repository:** {repo}
- **PR:** [{pr.get('html_url')}]({pr.get('html_url')})
- **Author:** `{author}`
- **Base:** `{(pr.get('base') or {}).get('ref', '')}`
- **Head:** `{(pr.get('head') or {}).get('ref', '')}`
- **State:** `{pr.get('state')}`{" draft" if pr.get("draft") else ""}

## Executive Read
{summary}

## Diff Intelligence
- **Analyzer:** `{analysis.get('engine', 'unknown')}`
- **Risk:** `{risk_level}` ({risk_score}/100)
- **Files:** {totals.get('files', len(files))}
- **Additions:** {totals.get('additions', 0)}
- **Deletions:** {totals.get('deletions', 0)}
- **Test files touched:** {totals.get('test_files', 0)}
- **Languages:** {lang_lines}

## Risk Signals
{signal_lines}

## Changed Files
{_render_file_table(files)}

## Review Plan
{rec_lines}

## Suggested Review Comments
- Ask the author to point to the focused tests or manual verification for the highest-risk paths.
- Confirm any dependency, config, or runtime behavior changes were intentional.
- Request a rollback note if this PR changes auth, data persistence, background jobs, or external integrations.

"""
    if ai_review:
        report += f"## AI Reviewer Synthesis\n{ai_review.strip()}\n\n"
    else:
        report += "## AI Reviewer Synthesis\nNo utility/default model is configured yet, so this report used deterministic analysis only.\n\n"

    report += f"## Mission Receipt\n{audit_lines}\n"
    return summary, report


@router.get("")
def list_missions(request: Request):
    user = require_user(request)
    owner = effective_user(request) or user
    db = SessionLocal()
    try:
        q = db.query(EngineeringMission).order_by(EngineeringMission.updated_at.desc())
        if owner:
            q = q.filter(EngineeringMission.owner == owner)
        missions = q.limit(30).all()
        return {"items": [_mission_to_dict(m, request=request) for m in missions]}
    finally:
        db.close()


def _get_owned_mission(db, mission_id: str, request: Request) -> EngineeringMission:
    user = require_user(request)
    owner = effective_user(request) or user
    mission = db.query(EngineeringMission).filter(EngineeringMission.id == mission_id).first()
    if not mission or (owner and mission.owner != owner):
        raise HTTPException(404, "Mission not found")
    return mission


def _get_public_mission(db, share_token: str) -> EngineeringMission:
    mission = (
        db.query(EngineeringMission)
        .filter(EngineeringMission.share_token == share_token)
        .filter(EngineeringMission.public_report == True)  # noqa: E712 - SQLAlchemy comparison
        .first()
    )
    if not mission:
        raise HTTPException(404, "Published mission not found")
    return mission


def _new_share_token(db) -> str:
    for _ in range(8):
        token = secrets.token_urlsafe(18).rstrip("=")
        exists = db.query(EngineeringMission.id).filter(EngineeringMission.share_token == token).first()
        if not exists:
            return token
    raise HTTPException(500, "Could not allocate a unique share token")


@router.get("/public/{share_token}")
def get_public_mission(share_token: str, request: Request):
    db = SessionLocal()
    try:
        mission = _get_public_mission(db, share_token)
        return _public_mission_to_dict(mission, request=request)
    finally:
        db.close()


@router.get("/public/{share_token}/export/markdown")
def export_public_mission_markdown(share_token: str):
    db = SessionLocal()
    try:
        return _markdown_export_response(_get_public_mission(db, share_token))
    finally:
        db.close()


@router.get("/public/{share_token}/export/json")
def export_public_mission_json(share_token: str, request: Request):
    db = SessionLocal()
    try:
        return _json_export_response(_get_public_mission(db, share_token), request=request)
    finally:
        db.close()


@router.get("/{mission_id}")
def get_mission(mission_id: str, request: Request):
    db = SessionLocal()
    try:
        return _mission_to_dict(_get_owned_mission(db, mission_id, request), request=request)
    finally:
        db.close()


@router.get("/{mission_id}/export/markdown")
def export_mission_markdown(mission_id: str, request: Request):
    db = SessionLocal()
    try:
        return _markdown_export_response(_get_owned_mission(db, mission_id, request))
    finally:
        db.close()


@router.get("/{mission_id}/export/json")
def export_mission_json(mission_id: str, request: Request):
    db = SessionLocal()
    try:
        return _json_export_response(_get_owned_mission(db, mission_id, request), request=request)
    finally:
        db.close()


@router.post("/{mission_id}/share")
def publish_mission_report(mission_id: str, request: Request):
    db = SessionLocal()
    try:
        mission = _get_owned_mission(db, mission_id, request)
        if mission.status != "completed" or not mission.report_markdown:
            raise HTTPException(409, "Only completed missions with a report can be published")
        if not mission.share_token:
            mission.share_token = _new_share_token(db)
        mission.public_report = True
        mission.published_at = utcnow_naive()
        mission.updated_at = utcnow_naive()
        db.add(mission)
        db.commit()
        db.refresh(mission)
        return _mission_to_dict(mission, request=request)
    finally:
        db.close()


@router.post("/{mission_id}/share/revoke")
def revoke_mission_report(mission_id: str, request: Request):
    db = SessionLocal()
    try:
        mission = _get_owned_mission(db, mission_id, request)
        mission.public_report = False
        mission.published_at = None
        mission.updated_at = utcnow_naive()
        db.add(mission)
        db.commit()
        db.refresh(mission)
        return _mission_to_dict(mission, request=request)
    finally:
        db.close()


@router.post("/pr-review")
async def create_pr_review(body: PRReviewRequest, request: Request):
    user = require_user(request)
    owner = effective_user(request) or user
    gh_owner, repo, number = _parse_pr_url(body.pr_url)
    mission = EngineeringMission(
        id=str(uuid.uuid4()),
        owner=owner,
        kind="pr_review",
        status="running",
        target_url=body.pr_url.strip(),
        title=f"PR Review: {gh_owner}/{repo}#{number}",
        audit_log=[],
        payload={"github": {"owner": gh_owner, "repo": repo, "number": number}},
    )
    db = SessionLocal()
    try:
        db.add(mission)
        db.commit()
        db.refresh(mission)
        _save_step(db, mission, _step("queued", "Mission created", detail="Stored mission shell and ownership scope."))
        try:
            pr, files = await _fetch_github_pr(gh_owner, repo, number)
            _save_step(db, mission, _step("fetch", "Fetched GitHub PR", detail=f"Loaded PR metadata and {len(files)} changed files."))
            analysis = _run_diff_analyzer(files)
            _save_step(db, mission, _step("analyze", "Analyzed diff with Go worker", detail=f"Risk {analysis.get('risk_level')} ({analysis.get('risk_score')}/100)."))
            pr_payload = _small_pr_payload(pr, files)
            ai_review = await _maybe_ai_review(owner or "", pr_payload, analysis) if body.include_ai else None
            _save_step(
                db,
                mission,
                _step(
                    "synthesize",
                    "Synthesized review report",
                    detail="Used configured model for synthesis." if ai_review else "No model synthesis used; deterministic report generated.",
                    meta={"ai_used": bool(ai_review)},
                ),
            )
            summary, report = _build_report(pr, files, analysis, ai_review, mission.audit_log or [])
            mission.status = "completed"
            mission.title = f"PR Review: {((pr.get('base') or {}).get('repo') or {}).get('full_name', gh_owner + '/' + repo)}#{number}"
            mission.summary = summary
            mission.report_markdown = report
            mission.payload = {"github": pr_payload, "analysis": analysis, "ai_used": bool(ai_review)}
            mission.finished_at = utcnow_naive()
            mission.updated_at = utcnow_naive()
            db.add(mission)
            db.commit()
            db.refresh(mission)
            _save_step(db, mission, _step("complete", "Mission completed", detail="Review report is ready to export."))
            return _mission_to_dict(mission, request=request)
        except HTTPException as exc:
            mission.status = "failed"
            mission.error = str(exc.detail)
            mission.finished_at = utcnow_naive()
            _save_step(db, mission, _step("failed", "Mission failed", "failed", str(exc.detail)))
            raise
        except Exception as exc:
            mission.status = "failed"
            mission.error = str(exc)
            mission.finished_at = utcnow_naive()
            _save_step(db, mission, _step("failed", "Mission failed", "failed", str(exc)))
            raise HTTPException(500, f"Mission failed: {exc}")
    finally:
        db.close()


def setup_engineering_mission_routes() -> APIRouter:
    return router
