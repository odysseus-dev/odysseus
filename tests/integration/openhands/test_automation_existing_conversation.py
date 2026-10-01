from pathlib import Path
import importlib.util
import sys

import pytest


ROOT = Path(__file__).resolve().parents[3]


def _load_probe():
    spec = importlib.util.spec_from_file_location(
        "openhands_probe", ROOT / "scripts/openhands_probe.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def probe():
    return _load_probe()


@pytest.fixture
def stack(probe):
    return probe.automation_stack()


_DISTINCT_RUN = pytest.mark.xfail(
    reason=(
        "Automation 1.11.0 is continued_run: GHCR pull unauthorized, no live "
        "execution IDs, documented dispatch accepts no conversation_id or "
        "request_key, and continue_conversation reports runs_created=[]"
    ),
    strict=True,
)


@_DISTINCT_RUN
def test_new_automation_execution_targets_existing_conversation(stack):
    first = stack.start_adhoc(message="first")
    stack.wait_idle(first.conversation_id)
    second = stack.start_adhoc(message="second", conversation_id=first.conversation_id)
    assert second.conversation_id == first.conversation_id
    assert second.execution_id != first.execution_id
    assert stack.execution(second.execution_id).request_key == second.request_key


@_DISTINCT_RUN
def test_canvas_created_conversation_can_be_targeted(stack):
    canvas = stack.create_canvas_conversation()
    run = stack.start_adhoc(
        message="from-canvas", conversation_id=canvas.conversation_id
    )
    assert run.conversation_id == canvas.conversation_id
    assert run.execution_id
    assert run.execution_id != canvas.conversation_id
    assert stack.execution(run.execution_id).conversation_id == canvas.conversation_id


@_DISTINCT_RUN
def test_stable_idempotency_key_replays_same_execution(stack):
    first = stack.start_adhoc(message="idempotent", request_key="gate-key-1")
    second = stack.start_adhoc(
        message="idempotent",
        request_key="gate-key-1",
        conversation_id=first.conversation_id,
    )
    assert second.execution_id == first.execution_id
    assert second.request_key == first.request_key == "gate-key-1"
    assert stack.execution(first.execution_id).request_key == "gate-key-1"


@_DISTINCT_RUN
def test_exact_run_cancellation(stack):
    run = stack.start_adhoc(message="cancel-me")
    cancelled = stack.cancel(run.execution_id)
    assert cancelled.execution_id == run.execution_id
    assert cancelled.status in {"cancelled", "canceled"}
    missing = stack.cancel("00000000-0000-0000-0000-000000000000")
    assert missing.status in {404, "not_found"}


@_DISTINCT_RUN
def test_adhoc_dispatch_does_not_create_disposable_definition(stack):
    before = stack.list_automation_definitions()
    run = stack.start_adhoc(message="no-definition")
    after = stack.list_automation_definitions()
    assert run.execution_id
    assert after == before
    assert stack.created_disposable_definition is False


def test_conversation_mode_enum_values(probe):
    mode = probe.AutomationConversationMode
    assert mode.DISTINCT_RUN == "distinct_run"
    assert mode.CONTINUED_RUN == "continued_run"
    assert mode.UNSUPPORTED == "unsupported"
    assert {item.value for item in mode} == {
        "distinct_run",
        "continued_run",
        "unsupported",
    }


def test_probe_classifies_explicit_conversation_mode(probe):
    result = probe.probe_automation_existing_conversation()
    assert result.name == "automation-existing-conversation"
    mode = result.evidence["classification"]
    assert mode in {item.value for item in probe.AutomationConversationMode}
    assert result.evidence["selected_branch"] == mode
    if mode != probe.AutomationConversationMode.DISTINCT_RUN:
        assert result.passed is False
        exception = result.evidence["interactive_exception"]
        assert "interactive Agent Server" in exception
        assert "explicit exception" in exception
    else:
        assert result.passed is True


def test_probe_cli_accepts_automation_existing_conversation(probe, monkeypatch):
    monkeypatch.setattr(
        sys,
        "argv",
        ["openhands_probe.py", "automation-existing-conversation", "--json"],
    )
    classified = probe.ProbeResult(
        name="automation-existing-conversation",
        passed=False,
        evidence={"classification": "unsupported"},
    )
    monkeypatch.setattr(
        probe, "probe_automation_existing_conversation", lambda: classified
    )
    assert probe.main() in {0, 1}
