from pathlib import Path


def test_agent_supervisor_setting_is_registered():
    from src.settings import DEFAULT_SETTINGS

    assert "agent_supervisor_ladder" in DEFAULT_SETTINGS
    assert DEFAULT_SETTINGS["agent_supervisor_ladder"] is False


def test_agent_loop_reads_ui_supervisor_setting():
    body = Path("src/agent_loop.py").read_text(encoding="utf-8")

    assert 'get_setting("agent_supervisor_ladder", False)' in body
    assert 'get_setting("agent_verifier_subagent", False)' in body


def test_settings_ui_uses_registered_supervisor_key():
    js = Path("static/js/settings.js").read_text(encoding="utf-8")
    html = Path("static/index.html").read_text(encoding="utf-8")

    assert "settings.agent_supervisor_ladder" in js
    assert "payload.agent_supervisor_ladder" in js
    assert "set-agentSupervisorLadder" in html
    assert "independently verified" in html


def test_manage_settings_accepts_supervisor_aliases():
    body = Path("src/tool_implementations.py").read_text(encoding="utf-8")

    assert '"supervisor ladder": "agent_supervisor_ladder"' in body
    assert '"agent verifier": "agent_supervisor_ladder"' in body
