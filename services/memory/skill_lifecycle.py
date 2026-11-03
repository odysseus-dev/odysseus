"""Bounded automatic review queue for user-owned procedural memory."""
import time


def automatic_audit_candidates(skills, limit=8, now=None):
    """Retry transient checks daily and failed repairs weekly, oldest first."""
    now = time.time() if now is None else now
    pending = []
    for skill in skills:
        if not skill.get("name") or skill.get("source") == "builtin" or skill.get("status") == "binned":
            continue
        verdict = skill.get("audit_verdict")
        if verdict in {"pass", "skipped"}:
            continue
        checked = float(skill.get("audited_at") or 0)
        delay = 7 * 86400 if verdict in {"fail", "needs_work"} else 86400
        if not verdict or now - checked >= delay:
            pending.append(skill)
    pending.sort(key=lambda skill: float(skill.get("audited_at") or 0))
    return pending[:max(1, limit)]
