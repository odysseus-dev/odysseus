import json

from scripts.build_odysseus_sft_repair_manifest import build_manifest
from scripts.build_odysseus_sft_repair_manifest import requires_native_workspace_tool


def test_generic_shell_version_check_is_native_workspace_only():
    assert requires_native_workspace_tool({
        "turns": [{"expect": "Run a shell version check and compare it."}],
    })


def row(seed, verdict="fail", owner="model_sft", *, offered=True, category="unanswered_followup"):
    return {
        "source_seed_id": seed,
        "family": "notes",
        "turns": [{"user": "show notes"}],
        "observed": [{
            "contract": {"offered": ["manage_notes"] if offered else []},
            "tool_calls": [],
        }],
        "judge": {
            "verdict": verdict, "owner": owner, "failure_category": category,
            "failed_turns": [1],
        },
    }


def save(path, rows, routing_experiment=None):
    payload = {"results": rows}
    if routing_experiment is not None:
        payload["routing_experiment"] = routing_experiment
    path.write_text(json.dumps(payload), encoding="utf-8")


def test_stochastic_pass_does_not_erase_clean_model_failure(tmp_path):
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    save(first, [row("fixed"), row("model"), row("routing", owner="harness_routing")])
    save(second, [row("fixed", verdict="pass"), row("absent", offered=False)])

    manifest = build_manifest([first, second], set())

    assert [item["source_seed_id"] for item in manifest["candidates"]] == ["fixed", "model"]
    reasons = {item["source_seed_id"]: item["reason"] for item in manifest["exclusions"]}
    assert reasons == {
        "absent": "no_failed_turn_with_executable_tool_surface",
    }


def test_verified_fix_requires_explicit_resolution(tmp_path):
    path = tmp_path / "run.json"
    save(path, [row("fixed")])

    manifest = build_manifest([path], set(), resolved_seeds={"fixed"})

    assert manifest["candidate_count"] == 0
    assert manifest["exclusions"] == [{
        "source_seed_id": "fixed",
        "reason": "explicitly_resolved_after_verified_fix",
    }]


def test_explicit_ambiguous_seed_is_excluded(tmp_path):
    path = tmp_path / "run.json"
    save(path, [row("ambiguous")])
    manifest = build_manifest([path], {"ambiguous"})
    assert manifest["candidate_count"] == 0
    assert manifest["exclusions"][0]["reason"] == "explicit_ambiguous_or_defective_seed"


def test_behavior_categories_are_normalized(tmp_path):
    path = tmp_path / "run.json"
    save(path, [row("wrong-action", category="wrong_tool_action")])
    manifest = build_manifest([path], set())
    assert manifest["candidates"][0]["behavior_category"] == "tool_action_selection"


def test_behavior_categories_cover_current_reusable_failure_classes():
    from scripts.build_odysseus_sft_repair_manifest import behavior_category

    expected = {
        "missing_required_tool_call": "required_tool_execution",
        "missing_required_action": "required_tool_execution",
        "unrecovered_command_failure": "tool_error_recovery",
        "missing_fallback_after_empty_read": "tool_error_recovery",
        "counting_error": "response_constraint_adherence",
        "instruction_following": "response_constraint_adherence",
        "missing_note_titles": "result_rendering",
        "missing_progress_link": "result_rendering",
        "suboptimal_command_selection": "tool_action_selection",
        "false_success": "evidence_grounding",
        "unfaithful_tool_summary": "evidence_grounding",
        "skill_content_mismatch": "evidence_grounding",
    }
    assert {name: behavior_category(name) for name in expected} == expected


def test_uncertain_latest_run_does_not_erase_valid_failure(tmp_path):
    first, second = tmp_path / "first.json", tmp_path / "second.json"
    save(first, [row("seed")])
    save(second, [row("seed", verdict="uncertain", owner="none")])
    manifest = build_manifest([first, second], set())
    assert [item["source_seed_id"] for item in manifest["candidates"]] == ["seed"]
    assert manifest["ignored_nonbehavioral_rows"] == 1


def test_manifest_excludes_failures_from_a_different_routing_runtime(tmp_path):
    baseline, exact = tmp_path / "baseline.json", tmp_path / "exact.json"
    save(baseline, [row("baseline-only")], "baseline")
    save(exact, [row("exact-runtime")], "recent_model_choice")

    manifest = build_manifest(
        [baseline, exact], set(), routing_experiment="recent_model_choice",
    )

    assert [item["source_seed_id"] for item in manifest["candidates"]] == ["exact-runtime"]
    assert manifest["ignored_runtime_inputs"] == 1


def test_manifest_excludes_webui_failures_that_require_native_workspace_tools(tmp_path):
    path = tmp_path / "run.json"
    native = row("native-only")
    native["turns"] = [{"user": "fix it", "expect": "Use edit_file on readme.md"}]
    save(path, [native])

    manifest = build_manifest([path], set())

    assert manifest["candidate_count"] == 0
    assert manifest["exclusions"] == [{
        "source_seed_id": "native-only",
        "reason": "requires_native_workspace_tool_on_webui_surface",
    }]
