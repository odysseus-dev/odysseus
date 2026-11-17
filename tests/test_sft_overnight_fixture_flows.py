import json
import os
from pathlib import Path
from types import SimpleNamespace

os.environ.setdefault("ODYSSEUS_QA_PASSWORD", "test")

from scripts import run_sft_overnight_fixture_flows as sft_flows


def test_load_existing_results_keeps_only_clean_allowed_rows(tmp_path: Path) -> None:
    out_dir = tmp_path / "run"
    out_dir.mkdir()
    payload = {
        "results": [
            {
                "id": "email_list_000",
                "domain": "email",
                "pass": True,
                "tool_names": ["mcp__email__list_emails"],
                "session_id": "old",
            },
            {
                "id": "email_list_000",
                "domain": "email",
                "pass": True,
                "tool_names": ["mcp__email__list_emails"],
                "session_id": "new",
            },
            {
                "id": "email_today_001",
                "domain": "email",
                "pass": True,
                "tool_names": ["mcp__email__list_emails", "manage_memory"],
            },
            {
                "id": "email_draft_reply_004",
                "domain": "email",
                "pass": True,
                "tool_names": ["ui_control", "ui_control"],
            },
            {
                "id": "notes_002",
                "domain": "notes",
                "pass": False,
                "tool_names": ["manage_notes"],
            },
            {
                "id": "calendar_003",
                "domain": "calendar",
                "pass": True,
                "tool_names": ["manage_calendar"],
            },
        ],
    }
    (out_dir / "actual_results.json").write_text(json.dumps(payload), encoding="utf-8")

    rows = sft_flows.load_existing_results(
        out_dir,
        allowed_ids={"email_list_000", "email_today_001", "email_draft_reply_004", "notes_002"},
    )

    assert [row["id"] for row in rows] == ["email_list_000"]
    assert rows[0]["session_id"] == "new"


def test_quarantine_sft_rows_moves_matching_session_to_trash(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sft_flows, "DATA_DIR", tmp_path)
    trace_dir = tmp_path / "sft_traces"
    trace_dir.mkdir()
    path = trace_dir / "maya.jsonl"
    path.write_text(
        "\n".join(
            [
                json.dumps({"session_id": "keep", "assistant": "ok"}),
                json.dumps({"session_id": "drop", "assistant": "bad"}),
                "{not-json",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    removed = sft_flows.quarantine_sft_rows("maya", "drop", "duplicate draft")

    assert removed == 1
    kept = path.read_text(encoding="utf-8").splitlines()
    assert json.loads(kept[0])["session_id"] == "keep"
    assert kept[1] == "{not-json"
    trash_rows = [
        json.loads(line)
        for line in (trace_dir / "maya.jsonl.trash").read_text(encoding="utf-8").splitlines()
    ]
    assert trash_rows == [
        {
            "session_id": "drop",
            "assistant": "bad",
            "deleted_from_training": True,
            "delete_reason": "duplicate draft",
        }
    ]


def test_score_rejects_duplicate_email_reply_draft_ui_control() -> None:
    case = {
        "id": "email_draft_reply_004",
        "expected_tools": ["ui_control"],
        "must_contain_any": ["draft"],
    }
    events = [
        {"type": "tool_start", "tool": "ui_control"},
        {"type": "tool_start", "tool": "ui_control"},
    ]

    passed, failures = sft_flows.score(case, events, "Draft opened.", {})

    assert not passed
    assert "duplicate_reply_draft_ui_control" in failures


def test_clean_counts_by_domain_counts_only_passing_known_domains() -> None:
    assert sft_flows.clean_counts_by_domain(
        [
            {"domain": "email", "pass": True},
            {"domain": "email", "pass": False},
            {"domain": "notes", "pass": True},
            {"domain": "calendar", "pass": True},
            {"domain": "other", "pass": True},
        ]
    ) == {"email": 1, "notes": 1, "calendar": 1}


def test_build_cases_uses_owner_specific_fixture_titles() -> None:
    maya_text = "\n".join([
        sft_flows.build_email_case(1, "sft_maya_ops")["must_contain_any"][0],
        sft_flows.build_note_case(1, "sft_maya_ops")["user"],
        sft_flows.build_calendar_case(1, "sft_maya_ops")["user"],
        " ".join(sft_flows.build_calendar_case(0, "sft_maya_ops")["must_contain_any"]),
    ])
    jules_text = "\n".join([
        sft_flows.build_email_case(1, "sft_jules_research")["must_contain_any"][0],
        sft_flows.build_note_case(1, "sft_jules_research")["user"],
        sft_flows.build_calendar_case(1, "sft_jules_research")["user"],
        " ".join(sft_flows.build_calendar_case(0, "sft_jules_research")["must_contain_any"]),
    ])
    nora_text = "\n".join([
        sft_flows.build_email_case(1, "sft_nora_design")["must_contain_any"][0],
        sft_flows.build_note_case(1, "sft_nora_design")["user"],
        sft_flows.build_calendar_case(0, "sft_nora_design")["must_contain_any"][0],
    ])
    omar_text = "\n".join([
        sft_flows.build_email_case(1, "sft_omar_finance")["must_contain_any"][0],
        sft_flows.build_note_case(1, "sft_omar_finance")["user"],
        sft_flows.build_calendar_case(0, "sft_omar_finance")["must_contain_any"][0],
    ])

    assert "creator operations" in maya_text
    assert "Customer success summary" in maya_text
    assert "Billing" in maya_text
    assert "research synthesis" in jules_text
    assert "Appendix cleanup" in jules_text
    assert "License" in jules_text
    assert "product design" in nora_text
    assert "Settings cleanup" in nora_text
    assert "Onboarding" in nora_text
    assert "finance planning" in omar_text
    assert "Contractor list" in omar_text
    assert "Leadership" in omar_text
    assert "OVN-JULES" in sft_flows.build_note_case(2, "sft_jules_research")["user"]


def test_email_draft_reply_cases_do_not_invite_signature_memory_lookup() -> None:
    case = sft_flows.build_email_case(4, "sft_nora_design")

    assert case["id"] == "email_draft_reply_004"
    assert "No signature needed" in case["user"]
    assert "manage_memory" in case["forbidden_tools"]


def test_note_update_cases_use_owner_name() -> None:
    assert "owner is Nora" in sft_flows.build_note_case(3, "sft_nora_design")["user"]
    assert "owner is Omar" in sft_flows.build_note_case(3, "sft_omar_finance")["user"]


def test_seed_case_removes_stale_marker_rows_before_seeding(monkeypatch) -> None:
    marker = "OVN-NORA-NOTE-018"
    stale_note = SimpleNamespace(id="stale", owner="sft_nora_design", title=marker, content=f"{marker} old")
    created = []
    deleted = []
    commits = []

    class _Query:
        def __init__(self, model):
            self.model = model

        def filter(self, *_args, **_kwargs):
            return self

        def join(self, *_args, **_kwargs):
            return self

        def all(self):
            if self.model is sft_flows.Note:
                return [stale_note]
            return []

        def first(self):
            return SimpleNamespace(id="cal")

    class _DB:
        def query(self, model):
            return _Query(model)

        def add(self, obj):
            created.append(obj)

        def delete(self, obj):
            deleted.append(obj)

        def commit(self):
            commits.append(True)

        def close(self):
            pass

    monkeypatch.setattr(sft_flows, "SessionLocal", lambda: _DB())

    seeded = sft_flows.seed_case(
        "sft_nora_design",
        {
            "marker": marker,
            "seed_note": {"title": marker, "content": f"{marker} initial"},
        },
    )

    assert deleted == [stale_note]
    assert len(created) == 1
    assert created[0].title == marker
    assert seeded["note_id"].startswith("sft-overnight-note-")
    assert len(commits) >= 2


def test_write_curated_trace_outputs_exports_run_specific_jsonl(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(sft_flows, "DATA_DIR", tmp_path)
    trace_dir = tmp_path / "sft_traces"
    trace_dir.mkdir()
    (trace_dir / "maya.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"session_id": "s1", "session_name": "SFT trace batch maya email 000", "thinking": "t", "tool_events": [{}]}),
                json.dumps({"session_id": "s2", "session_name": "SFT trace batch maya notes 000", "thinking": "", "tool_events": [{}]}),
                json.dumps({"session_id": "old", "session_name": "SFT trace batch maya email 999", "thinking": "old"}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    out_dir = tmp_path / "evals" / "run-1"
    out_dir.mkdir(parents=True)
    args = SimpleNamespace(owner="maya", out_dir=out_dir)
    results = [
        {"session_id": "s1", "id": "email_000", "domain": "email", "pass": True},
        {"session_id": "s2", "id": "notes_000", "domain": "notes", "pass": True},
        {"session_id": "failed", "id": "calendar_000", "domain": "calendar", "pass": False},
    ]

    info = sft_flows.write_curated_trace_outputs(args, results)

    assert info["skipped"] is False
    curated_path = trace_dir / "maya.run-1.curated.jsonl"
    thinking_path = trace_dir / "maya.run-1.curated_thinking.jsonl"
    assert [json.loads(line)["session_id"] for line in curated_path.read_text(encoding="utf-8").splitlines()] == ["s1", "s2"]
    assert [json.loads(line)["session_id"] for line in thinking_path.read_text(encoding="utf-8").splitlines()] == ["s1"]
    assert json.loads((out_dir / "curated_trace_summary.json").read_text(encoding="utf-8"))["rows"] == 2
    thinking_summary = json.loads((out_dir / "curated_thinking_trace_summary.json").read_text(encoding="utf-8"))
    assert thinking_summary["skipped_no_thinking"] == 1
    assert thinking_summary["missing_without_reason"] == 0


def test_atomic_write_text_preserves_existing_file_on_write_failure(tmp_path: Path, monkeypatch) -> None:
    path = tmp_path / "actual_results.json"
    path.write_text("previous", encoding="utf-8")
    real_write_text = Path.write_text

    def fail_tmp_write(self, *args, **kwargs):
        if self.name.startswith(".actual_results.json."):
            raise OSError("disk full")
        return real_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", fail_tmp_write)

    try:
        sft_flows.atomic_write_text(path, "next")
    except OSError:
        pass

    assert path.read_text(encoding="utf-8") == "previous"
