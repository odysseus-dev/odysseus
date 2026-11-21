"""Regression tests for Deep Research tool owner isolation."""

import asyncio
import json
import pytest
import src.tools.research as research_tools


def _write_report(dr_dir, rid, owner, query, body="Secret findings"):
    (dr_dir / f"{rid}.json").write_text(
        json.dumps({
            "query": query,
            "owner": owner,
            "result": body,
            "completed_at": 1700000000,
            "sources": [{"url": "https://example.com", "title": "Example"}],
        }),
        encoding="utf-8",
    )


def test_manage_research_requires_authenticated_owner(monkeypatch, tmp_path):
    monkeypatch.setattr(research_tools, "DEEP_RESEARCH_DIR", str(tmp_path))
    _write_report(tmp_path, "rep-alice", "alice", "Alice private report")

    for owner in ("", "   "):
        res = asyncio.run(research_tools.do_manage_research('{"action": "list"}', owner=owner))
        assert res["exit_code"] == 1
        assert "owner is required" in res["error"].lower()


def test_manage_research_list_read_and_delete_are_owner_scoped(monkeypatch, tmp_path):
    monkeypatch.setattr(research_tools, "DEEP_RESEARCH_DIR", str(tmp_path))
    _write_report(tmp_path, "rep-alice", "alice", "Alice confidential topic", body="Alice secret")
    _write_report(tmp_path, "rep-bob", "bob", "Bob confidential topic", body="Bob secret")
    _write_report(tmp_path, "rep-legacy", "", "Ownerless legacy report", body="Legacy secret")

    # Alice sees only her own report in list
    listed = asyncio.run(research_tools.do_manage_research('{"action": "list"}', owner="alice"))
    assert listed["exit_code"] == 0
    assert "Alice confidential topic" in listed["output"]
    assert "Bob confidential topic" not in listed["output"]
    assert "Ownerless legacy report" not in listed["output"]

    # Alice cannot read Bob's or ownerless reports
    for forbidden_id in ("rep-bob", "rep-legacy"):
        read_res = asyncio.run(
            research_tools.do_manage_research(
                json.dumps({"action": "read", "id": forbidden_id}),
                owner="alice",
            )
        )
        assert read_res["exit_code"] == 1
        assert "access denied" in read_res["error"].lower()

    # Alice cannot delete Bob's report
    del_bob = asyncio.run(
        research_tools.do_manage_research(
            json.dumps({"action": "delete", "id": "rep-bob"}),
            owner="alice",
        )
    )
    assert del_bob["exit_code"] == 1
    assert (tmp_path / "rep-bob.json").exists()

    # Alice can read and delete her own report
    read_own = asyncio.run(
        research_tools.do_manage_research('{"action": "read", "id": "rep-alice"}', owner="alice")
    )
    assert "Alice secret" in read_own["output"]

    del_own = asyncio.run(
        research_tools.do_manage_research('{"action": "delete", "id": "rep-alice"}', owner="alice")
    )
    assert del_own["exit_code"] == 0
    assert not (tmp_path / "rep-alice.json").exists()
