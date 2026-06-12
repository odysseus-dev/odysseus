"""PR #3681 follow-up — every email tool path receives the saved writing style.

`_EMAIL_TOOL_HINTS` in `_build_system_prompt` gates the email-context branch:
the user's saved writing style plus the hard identity/mechanical email rules.
It was the last hand-maintained copy of the email tool list, so the draft
tools this consolidation exposes (`draft_email`, `draft_email_reply`,
`ai_draft_email_reply` — the PREFERRED way to write email) skipped the saved
style entirely while the older send/read paths received it. The set now
derives from BUILTIN_EMAIL_TOOLS in both spellings; these tests pin that for
every email tool, bare and mcp__email__-qualified.
"""
import src.agent_tools  # noqa: F401 — resolve the circular-import cluster first
import src.agent_loop as al
from src.tool_security import BUILTIN_EMAIL_TOOLS

STYLE_SENTINEL = "STYLE-SENTINEL-FREUNDLICHE-GRUESSE"


def _system_prompt_for(monkeypatch, relevant_tools, user_text="draft an email reply to Bob"):
    import src.settings as settings_mod

    monkeypatch.setattr(
        settings_mod, "load_settings",
        lambda: {"email_writing_style": STYLE_SENTINEL},
    )
    # Bust the base-prompt cache so each call rebuilds deterministically.
    al._cached_base_prompt = None
    al._cached_base_prompt_key = None

    out, _ = al._build_system_prompt(
        [{"role": "user", "content": user_text}],
        "test-model", None, None, set(),
        relevant_tools=set(relevant_tools), owner=None,
    )
    return "\n".join(
        str(m.get("content", "")) for m in out if m.get("role") == "system"
    )


def test_draft_tools_receive_saved_writing_style(monkeypatch):
    # The exact gap from review: a draft-only tool selection must still get
    # the saved style + identity rules.
    for tool in ("draft_email", "draft_email_reply", "ai_draft_email_reply"):
        prompt = _system_prompt_for(monkeypatch, {tool})
        assert STYLE_SENTINEL in prompt, f"{tool} skipped the saved writing style"
        assert "Hard identity rule" in prompt, f"{tool} skipped the identity rules"


def test_every_email_tool_spelling_triggers_style_injection(monkeypatch):
    for tool in sorted(BUILTIN_EMAIL_TOOLS):
        for name in (tool, f"mcp__email__{tool}"):
            prompt = _system_prompt_for(monkeypatch, {name})
            assert STYLE_SENTINEL in prompt, f"{name} skipped the saved writing style"


def test_non_email_tools_do_not_inject_style(monkeypatch):
    prompt = _system_prompt_for(monkeypatch, {"bash"}, user_text="send a quick email to Bob")
    assert STYLE_SENTINEL not in prompt


def test_email_tool_without_emailish_request_does_not_inject_style(monkeypatch):
    # The existing wording gate stays intact: an email tool selected for an
    # unrelated request doesn't drag the style block in.
    prompt = _system_prompt_for(monkeypatch, {"draft_email"}, user_text="open the settings panel")
    assert STYLE_SENTINEL not in prompt
