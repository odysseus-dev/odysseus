import pytest
from fastapi import HTTPException
from types import SimpleNamespace

from routes.engineering_mission_routes import _fallback_analysis, _parse_pr_url, _report_filename


def test_parse_pr_url_accepts_canonical_github_url():
    owner, repo, number = _parse_pr_url("https://github.com/example-org/app/pull/42")

    assert owner == "example-org"
    assert repo == "app"
    assert number == 42


def test_parse_pr_url_rejects_non_pr_url():
    with pytest.raises(HTTPException):
        _parse_pr_url("https://github.com/example-org/app/issues/42")


def test_fallback_analysis_flags_missing_tests_and_sensitive_patch():
    analysis = _fallback_analysis([
        {
            "filename": "routes/auth_routes.py",
            "status": "modified",
            "additions": 12,
            "deletions": 2,
            "changes": 14,
            "patch": "+ subprocess.run(command, shell=True)",
        }
    ])

    labels = {signal["label"] for signal in analysis["risk_signals"]}
    assert analysis["risk_level"] in {"medium", "high"}
    assert "No test files changed" in labels
    assert "Sensitive API touched" in labels


def test_report_filename_is_safe_and_traceable():
    mission = SimpleNamespace(id="12345678-90ab-cdef", title="PR Review: owner/repo#99")

    assert _report_filename(mission, "md") == "pr-review-owner-repo-99-12345678.md"
