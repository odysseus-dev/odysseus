from services.memory.skill_lifecycle import automatic_audit_candidates


def test_automatic_review_retries_without_starving_new_skills():
    now = 20 * 86400
    rows = [
        {"name": "builtin", "source": "builtin"},
        {"name": "bin", "status": "binned"},
        {"name": "approved", "audit_verdict": "pass"},
        {"name": "duplicate", "audit_verdict": "skipped"},
        {"name": "recent", "audit_verdict": "inconclusive", "audited_at": now - 60},
        {"name": "failed", "audit_verdict": "fail", "audited_at": now - 8 * 86400},
        {"name": "transient", "audit_verdict": "inconclusive", "audited_at": now - 2 * 86400},
        {"name": "new"},
    ]
    assert [s["name"] for s in automatic_audit_candidates(rows, now=now)] == ["new", "failed", "transient"]
    assert [s["name"] for s in automatic_audit_candidates(rows, limit=1, now=now)] == ["new"]
