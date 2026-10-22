"""Audit publication and duplicate coordination through the canonical SkillsManager."""

import logging
import re
from typing import Optional

from .dependencies import AuditDependencies

logger = logging.getLogger(__name__)

def auto_publish_policy(owner) -> tuple[bool, float]:
    """Return (auto_publish_enabled, minimum_confidence) for audit finalization."""
    try:
        from routes.prefs_routes import _load_for_user
        prefs = _load_for_user(owner) or {}
    except Exception:
        prefs = {}
    try:
        from src.settings import get_setting
        default_min = get_setting("skill_autosave_min_confidence", 0.85)
    except Exception:
        default_min = 0.85
    enabled = bool(prefs.get("auto_approve_skills", True))
    try:
        min_conf = float(prefs.get("skill_min_confidence", default_min))
    except (TypeError, ValueError):
        min_conf = 0.85
    return enabled, max(0.0, min(1.0, min_conf))


def duplicate_blocker(skills_manager, name: str, owner) -> Optional[str]:
    """Cheap duplicate guard matching the UI's duplicate grouping.

    The LLM necessity check catches semantic redundancy, but the UI also has a
    cheap similarity pass. Use the same broad signal before auto-publishing so
    a high-scoring lower-priority duplicate stays draft.
    """
    import re as _re

    def _tokens(sk: dict) -> set[str]:
        text = " ".join([
            str(sk.get("name") or ""),
            str(sk.get("description") or ""),
            str(sk.get("when_to_use") or ""),
            " ".join(sk.get("procedure") or []),
            " ".join(sk.get("tags") or []),
        ]).lower()
        text = _re.sub(r"-\d+\b", "", text)
        return {
            t for t in _re.split(r"[^a-z0-9]+", text)
            if len(t) > 2 and t not in {"the", "and", "with", "for", "from", "using"}
        }

    def _sim(a: dict, b: dict) -> float:
        A, B = _tokens(a), _tokens(b)
        if not A or not B:
            return 0.0
        return len(A & B) / max(1, len(A | B))

    def _base(n: str) -> str:
        return _re.sub(r"-\d+$", "", str(n or ""))

    def _score(sk: dict) -> float:
        return (
            (100000 if (sk.get("status") == "published") else 0)
            + int(sk.get("uses") or 0) * 100
            + round(float(sk.get("confidence") or 0) * 100)
            + (-5 if sk.get("audit_by_teacher") else 0)
            - (len(str(sk.get("name") or "")) / 1000)
        )

    skills = [s for s in skills_manager.load(owner=owner) if s.get("status") != "binned"]
    current = next((s for s in skills if (s.get("name") or s.get("id")) == name), None)
    if not current:
        return None
    duplicates = []
    cur_name = current.get("name") or current.get("id") or name
    for other in skills:
        other_name = other.get("name") or other.get("id")
        if not other_name or other_name == cur_name:
            continue
        if _base(cur_name) == _base(other_name) or _sim(current, other) >= 0.38:
            duplicates.append(other)
    if not duplicates:
        return None
    keeper = sorted([current, *duplicates], key=_score, reverse=True)[0]
    keeper_name = keeper.get("name") or keeper.get("id") or ""
    if keeper_name and keeper_name != cur_name:
        try:
            skills_manager.set_necessity(
                cur_name,
                False,
                [keeper_name],
                f"Lower-priority duplicate of {keeper_name}",
                owner=owner,
            )
        except Exception:
            pass
        return keeper_name
    return None


def finalize_batch(skills_manager, results: list[dict], owner, log, *, deps: AuditDependencies) -> None:
    """Draft audited failures, bin duplicate losers, and publish the best copy.

    Binned skills remain on disk for recovery and inspection, but the Skills
    manager excludes them from retrieval. Only skills actually processed by
    this audit job are changed here; an unrelated existing skill is never
    moved just because it resembles an audited one.
    """
    import re as _re

    auto_publish, min_conf = deps.auto_publish_policy(owner)
    current = [
        s for s in skills_manager.load(owner=owner)
        if s.get("source") != "builtin" and s.get("status") != "binned"
    ]
    by_name = {s.get("name"): s for s in current if s.get("name")}
    processed = {str(r.get("skill")) for r in results if r.get("skill")}
    protected = {
        str(r.get("skill")) for r in results
        if r.get("skill") and r.get("result") == "approval_required"
    }

    def tokens(sk: dict) -> set[str]:
        text = " ".join([
            str(sk.get("name") or ""), str(sk.get("description") or ""),
            str(sk.get("when_to_use") or ""), " ".join(sk.get("procedure") or []),
            " ".join(sk.get("tags") or []),
        ]).lower()
        text = _re.sub(r"-\d+\b", "", text)
        return {
            t for t in _re.split(r"[^a-z0-9]+", text)
            if len(t) > 2 and t not in {"the", "and", "with", "for", "from", "using"}
        }

    def similar(a: dict, b: dict) -> float:
        left, right = tokens(a), tokens(b)
        return len(left & right) / max(1, len(left | right)) if left and right else 0.0

    def base(name: str) -> str:
        return _re.sub(r"-\d+$", "", str(name or ""))

    def score(sk: dict) -> float:
        try:
            confidence = float(sk.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        return (
            (100000 if sk.get("status") == "published" else 0)
            + int(sk.get("uses") or 0) * 100
            + round(confidence * 100)
            + (-5 if sk.get("audit_by_teacher") else 0)
            - len(str(sk.get("name") or "")) / 1000
        )

    # Anything that does not clear the configured policy stays a draft. Drafts
    # are excluded from retrieval/injection by SkillsManager.index_for().
    for name in processed - protected:
        skill = by_name.get(name)
        if not skill:
            continue
        verdict = str(skill.get("audit_verdict") or "").lower()
        try:
            confidence = float(skill.get("confidence") or 0)
        except (TypeError, ValueError):
            confidence = 0.0
        if verdict in {"needs_work", "fail"} or (
            verdict == "pass" and confidence < min_conf
        ):
            try:
                skills_manager.update_skill(name, {"status": "draft"}, owner=owner)
                log(f"{name}: kept as draft after audit")
            except Exception:
                logger.warning("Could not bin audited skill %s", name, exc_info=True)

    # Build the same connected duplicate groups shown by the UI.
    parent = {s["name"]: s["name"] for s in current}

    def find(name: str) -> str:
        while parent[name] != name:
            parent[name] = parent[parent[name]]
            name = parent[name]
        return name

    def unite(left: str, right: str) -> None:
        left, right = find(left), find(right)
        if left != right:
            parent[right] = left

    for index, left in enumerate(current):
        for right in current[index + 1:]:
            if base(left["name"]) == base(right["name"]) or similar(left, right) >= 0.38:
                unite(left["name"], right["name"])
    groups: dict[str, list[dict]] = {}
    for skill in current:
        groups.setdefault(find(skill["name"]), []).append(skill)

    for group in groups.values():
        if len(group) < 2:
            continue
        passing = []
        for skill in group:
            if skill["name"] not in processed or skill["name"] in protected:
                continue
            if str(skill.get("audit_verdict") or "").lower() != "pass":
                continue
            try:
                confidence = float(skill.get("confidence") or 0)
            except (TypeError, ValueError):
                confidence = 0.0
            if confidence >= min_conf:
                passing.append(skill)
        if not passing:
            continue
        keeper = max(passing, key=score)
        if auto_publish:
            try:
                skills_manager.update_skill(keeper["name"], {"status": "published"}, owner=owner)
                log(f"{keeper['name']}: auto-approved as best passing duplicate")
            except Exception:
                logger.warning("Could not auto-approve skill %s", keeper["name"], exc_info=True)
        for skill in group:
            name = skill["name"]
            if name == keeper["name"] or name not in processed or name in protected:
                continue
            try:
                skills_manager.update_skill(name, {"status": "binned"}, owner=owner)
                log(f"{name}: moved to bin as duplicate of {keeper['name']}")
            except Exception:
                logger.warning("Could not bin duplicate skill %s", name, exc_info=True)


def audit_flag_text(*parts) -> str:
    text_parts = []
    for part in parts:
        if isinstance(part, dict):
            text_parts.extend(str(v or "") for v in part.values())
        elif isinstance(part, (list, tuple, set)):
            text_parts.extend(str(v or "") for v in part)
        else:
            text_parts.append(str(part or ""))
    return " ".join(text_parts).lower()


def utility_blocker(skill: Optional[dict], necessity: Optional[dict],
                           verdict_data: Optional[dict]) -> Optional[str]:
    """Return a short reason when a passing skill still should stay draft.

    Broad/generic wording is not a blocker by itself. The blocker is whether
    the skill failed to improve the agent versus a no-skill baseline, or whether
    it duplicates another skill.
    """
    generic_re = re.compile(
        r"\b(duplicat\w*|redundan\w*|overlap\w*|same skill|same procedure)\b",
        re.I,
    )
    if isinstance(necessity, dict):
        reason = str(necessity.get("reason") or "")
        if necessity.get("necessary") is False and generic_re.search(reason):
            return reason or "Duplicate or redundant skill"

    if isinstance(verdict_data, dict):
        baseline_verdict = str(verdict_data.get("baseline_verdict") or "unknown").lower()
        try:
            usefulness = float(verdict_data.get("usefulness", 0.0) or 0.0)
        except (TypeError, ValueError):
            usefulness = 0.0
        try:
            saved_turns = int(verdict_data.get("saved_turns", 0) or 0)
        except (TypeError, ValueError):
            saved_turns = 0
        try:
            saved_tool_calls = int(verdict_data.get("saved_tool_calls", 0) or 0)
        except (TypeError, ValueError):
            saved_tool_calls = 0
        if baseline_verdict == "worse":
            return "Skill performed worse than the no-skill baseline"
        verdict_text = audit_flag_text(
            verdict_data.get("summary"),
            verdict_data.get("issues") or [],
        )
        if generic_re.search(verdict_text):
            return "Audit flagged the skill as duplicate or redundant"
    return None


def finalize_status(skills_manager, name: str, owner, verdict: str,
                           confidence: Optional[float], necessity: Optional[dict] = None,
                           verdict_data: Optional[dict] = None, *, deps: AuditDependencies) -> str:
    """Apply the user's audit publishing policy.

    Audit is the final pass: skills that pass at/above the threshold are
    published; failing or unnecessary/redundant skills are returned to draft.
    Inconclusive runs preserve the existing state because they provide no
    evidence either way. The completed batch moves duplicate losers to the bin.
    """
    auto_publish, min_conf = deps.auto_publish_policy(owner)
    necessary = True
    current = next((s for s in skills_manager.load(owner=owner) if s.get("name") == name), None)
    if verdict in {"inconclusive", "unknown"}:
        return (current or {}).get("status") or "draft"
    utility_reason = deps.utility_blocker(current, necessity, verdict_data)
    if (
        isinstance(necessity, dict)
        and necessity.get("necessary") is False
        and necessity.get("redundant_with")
    ):
        necessary = False
    if utility_reason:
        necessary = False
        try:
            skills_manager.set_necessity(name, False, [], utility_reason, owner=owner)
        except Exception:
            pass
    duplicate_of = deps.duplicate_blocker(skills_manager, name, owner) if verdict == "pass" else None
    if duplicate_of:
        necessary = False
    c = float(confidence or 0.0)
    status = "published" if (auto_publish and necessary and verdict == "pass" and c >= min_conf) else "draft"
    try:
        skills_manager.update_skill(name, {"status": status}, owner=owner)
    except Exception:
        pass
    return status


def apply_markdown(skills_manager, name: str, md: str, owner) -> bool:
    """Parse + persist an edited SKILL.md. Returns True on success."""
    try:
        from services.memory.skill_format import Skill, slugify
        sk = Skill.from_markdown(md)
        # Pin the identity: the audit's fixer is now allowed to edit frontmatter
        # (tags/category/when_to_use/description), but it must NEVER rename the
        # skill — a changed `name` would move the dir and orphan the usage/audit
        # sidecar entries that the caller keeps writing under the original name.
        sk.name = name
        return bool(skills_manager.update_skill(name, {
            "name": sk.name, "description": sk.description, "version": sk.version,
            "category": sk.category, "tags": sk.tags, "platforms": sk.platforms,
            "requires_toolsets": sk.requires_toolsets, "fallback_for_toolsets": sk.fallback_for_toolsets,
            "status": sk.status, "confidence": sk.confidence, "source": sk.source,
            "teacher_model": sk.teacher_model, "owner": sk.owner or owner,
            "when_to_use": sk.when_to_use, "procedure": sk.procedure,
            "pitfalls": sk.pitfalls, "verification": sk.verification, "body_extra": sk.body_extra,
        }, owner=owner))
    except Exception as e:
        logger.warning(f"Audit: could not save edited skill {name}: {e}")
        return False
