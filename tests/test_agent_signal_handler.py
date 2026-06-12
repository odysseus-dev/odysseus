import pytest

from src.builtin_actions import TaskNoop, _agent_handle_email_received


def _payload(**overrides):
    payload = {
        "event": "email_received",
        "subject": "Can we reschedule our Thursday meeting?",
        "from_name": "Sarah Chen",
        "from_address": "sarah@example.com",
        "account": "default",
    }
    payload.update(overrides)
    return payload


def _patch_llm(monkeypatch, verdict):
    monkeypatch.setattr(
        "src.endpoint_resolver.resolve_endpoint",
        lambda purpose, owner=None: ("https://example.test/v1/chat/completions", "test-model", {}),
    )

    async def fake_llm_call_async(**kwargs):
        return verdict

    monkeypatch.setattr("src.llm_core.llm_call_async", fake_llm_call_async)
    monkeypatch.setattr("src.assistant_log.log_to_assistant", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_agent_signal_email_llm_needs_action_succeeds(monkeypatch):
    _patch_llm(monkeypatch, "NEEDS_ACTION: asks a direct scheduling question.")

    result, success = await _agent_handle_email_received("admin", _payload())

    assert success is True
    assert result.startswith("Email analysed: NEEDS_ACTION")


@pytest.mark.asyncio
async def test_agent_signal_email_parses_verdict_inside_prose(monkeypatch):
    _patch_llm(monkeypatch, "This is a failed pipeline notice. Verdict: NEEDS_ACTION because CI is broken.")

    result, success = await _agent_handle_email_received(
        "admin",
        _payload(subject="gitlab-test | Failed pipeline for main | dad023c0", from_name="GitLab"),
    )

    assert success is True
    assert result.startswith("Email analysed:")


@pytest.mark.asyncio
async def test_agent_signal_email_failed_pipeline_fallback_succeeds(monkeypatch):
    _patch_llm(monkeypatch, "This looks important but I cannot choose one label.")

    result, success = await _agent_handle_email_received(
        "admin",
        _payload(subject="gitlab-test | Failed pipeline for main | dad023c0", from_name="GitLab"),
    )

    assert success is True
    assert result == "New email needs action: GitLab — gitlab-test | Failed pipeline for main | dad023c0"


@pytest.mark.asyncio
async def test_agent_signal_email_llm_informational_skips(monkeypatch):
    _patch_llm(monkeypatch, "INFORMATIONAL: this is a newsletter.")

    with pytest.raises(TaskNoop) as exc:
        await _agent_handle_email_received("admin", _payload(subject="Architecture & Design Round-Up"))

    assert "Informational email from Sarah Chen" in str(exc.value)


@pytest.mark.asyncio
async def test_agent_signal_email_llm_skip_skips(monkeypatch):
    _patch_llm(monkeypatch, "SKIP: automated notification.")

    with pytest.raises(TaskNoop) as exc:
        await _agent_handle_email_received("admin", _payload(subject="Build finished"))

    assert "Skipped email from Sarah Chen" in str(exc.value)


@pytest.mark.asyncio
async def test_agent_signal_email_auto_generated_sender_skips_before_llm(monkeypatch):
    async def fail_if_called(**kwargs):
        raise AssertionError("LLM should not be called for auto-generated sender")

    monkeypatch.setattr("src.llm_core.llm_call_async", fail_if_called)

    with pytest.raises(TaskNoop) as exc:
        await _agent_handle_email_received(
            "admin",
            _payload(
                subject='[GitHub] The "Ansible Molecule" workflow failed',
                from_name="GitHub",
                from_address="noreply@github.com",
            ),
        )

    assert "Auto-generated email from noreply@github.com" in str(exc.value)
