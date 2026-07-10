import json
from pathlib import Path

from scripts.run_misumi_evals import evaluate_case


def test_eval_fixture_set_covers_required_behaviours():
    path = Path(__file__).parents[1] / "evals" / "misumi" / "fixtures.json"
    cases = json.loads(path.read_text(encoding="utf-8"))
    ids = {case["id"] for case in cases}
    assert ids == {
        "capabilities", "autonomous-task-routing", "shopping-list", "route-sanji",
        "route-kurisu", "jin-shell-blocked", "next-task", "blocked-state",
        "phase-a-write-refusal", "external-import-manual",
    }


def test_eval_assertion_engine_supports_nested_policy_checks():
    case = {"assert": {"policy.writes_allowed": False}, "assert_contains": {"policy.tools_blocked": "bash"}}
    payload = {"policy": {"writes_allowed": False, "tools_blocked": ["bash"]}}
    assert evaluate_case(case, payload) == []
