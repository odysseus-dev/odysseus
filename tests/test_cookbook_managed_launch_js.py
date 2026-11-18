"""Managed Cookbook launch: unknown memory fit needs an explicit confirmation, and the chat picker follows real readiness."""
from pathlib import Path

SERVE = Path(__file__).resolve().parents[1] / "static" / "js" / "cookbookServe.js"


def _launch_source() -> str:
    source = SERVE.read_text(encoding="utf-8")
    start = source.index("async function _launchManagedLlama(")
    return source[start:source.index("\n}\n", start)]


def test_plan_is_requested_before_start_and_start_is_never_skipped_ahead():
    src = _launch_source()
    assert src.index("/api/cookbook/serving/plan") < src.index("/api/cookbook/serving/start")


def test_unknown_fit_requires_confirmation_and_cancel_spawns_nothing():
    src = _launch_source()
    check = src.index("plan.memory_validation === 'unknown'")
    confirm = src.index("window.styledConfirm(", check)
    cancel = src.index("if (!proceed)", confirm)
    ack = src.index("body.allow_unestimated_context = true", cancel)
    start = src.index("/api/cookbook/serving/start")
    # cancel returns before the acknowledgement is set and before the start request exists
    assert check < confirm < cancel < ack < start
    assert "return true;" in src[cancel:ack]
    assert "Nothing starts unless you confirm" in src[confirm:cancel]


def test_acknowledgement_is_set_only_by_the_confirmation_branch():
    src = _launch_source()
    assert src.count("allow_unestimated_context") == 1
    assert "allow_unestimated_context: true" not in src


def test_failed_plan_starts_nothing():
    src = _launch_source()
    failed = src.index("if (!planRes.ok)")
    assert "return true;" in src[failed:src.index("plan.memory_validation")]


def test_picker_follows_typed_readiness_with_endpoint_id_and_runtime_models():
    source = SERVE.read_text(encoding="utf-8")
    start = source.index("async function _followManagedReadiness(")
    follow = source[start:source.index("\n}\n", start)]
    assert "/api/cookbook/serving/wait-ready" in follow
    assert "refreshModels(true" in follow                      # forced probe, so the runtime's own ids are listed
    assert "endpointId" in follow and "verdict.modelIds" in follow   # endpoint_id and verified runtime model ids reach listeners
    assert "verifyManagedSelectable" in follow                 # wait-ready alone never makes a model selectable
    assert "is not ready" in follow                             # a non-ready outcome is reported, not hidden
    assert "_followManagedReadiness(data.session_id, data.endpoint_id, name)" in _launch_source()
