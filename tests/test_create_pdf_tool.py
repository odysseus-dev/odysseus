"""create_pdf agent tool: render chat content and store it as the owner's upload."""

import asyncio
import io
import json
from types import SimpleNamespace

import pytest
from pypdf import PdfReader


class _Sessions:
    def __init__(self, history):
        self._session = SimpleNamespace(history=history)

    def get_session(self, session_id):
        if session_id != "s1":
            raise KeyError(session_id)
        return self._session


def _msg(role, content):
    return SimpleNamespace(role=role, content=content)


@pytest.fixture
def env(tmp_path, monkeypatch):
    from src import constants
    from src.upload_handler import UploadHandler
    import src.agent_tools.pdf_tools as pdf_tools

    upload_dir = tmp_path / "uploads"
    upload_dir.mkdir()
    monkeypatch.setattr(constants, "UPLOAD_DIR", str(upload_dir))
    handler = UploadHandler(str(tmp_path), str(upload_dir))
    history = [
        _msg("user", "how do I fix 0xc0000001?"),
        _msg("assistant", "## Steps\n\n1. Boot from USB\n2. Run `bootrec /rebuildbcd`"),
        _msg("user", "turn those instructions into a pdf"),
    ]
    monkeypatch.setattr(pdf_tools, "get_upload_handler", lambda: handler)
    monkeypatch.setattr(pdf_tools, "get_session_manager", lambda: _Sessions(history))
    return SimpleNamespace(handler=handler, history=history)


def _run(content, session_id="s1", owner="alice"):
    from src.agent_tools import TOOL_HANDLERS
    return asyncio.run(TOOL_HANDLERS["create_pdf"](content, {"session_id": session_id, "owner": owner}))


def _pdf_text(env, attachment):
    info = next(row for row in env.handler._load_upload_index().values() if row["id"] == attachment["id"])
    with open(info["path"], "rb") as fh:
        reader = PdfReader(io.BytesIO(fh.read()))
    return info, "\n".join(page.extract_text() for page in reader.pages)


def test_creates_an_owned_pdf_attachment(env):
    result = _run(json.dumps({"title": "Boot fix", "content_markdown": "Run **bootrec**."}))

    assert result["exit_code"] == 0
    attachment = result["attachment"]
    assert attachment["mime"] == "application/pdf"
    assert attachment["name"] == "Boot_fix.pdf"
    assert attachment["size"] > 0
    assert "Boot_fix.pdf" in result["output"]
    info, text = _pdf_text(env, attachment)
    assert info["owner"] == "alice"
    assert "Boot fix" in text and "bootrec" in text


def test_empty_content_uses_the_previous_assistant_answer(env):
    result = _run(json.dumps({"title": "Boot fix", "content_markdown": ""}))

    assert result["exit_code"] == 0
    _, text = _pdf_text(env, result["attachment"])
    assert "Boot from USB" in text and "bootrec /rebuildbcd" in text


DATE_ECHO = (
    "**Today is Thursday, October 29, 2026 (2026-10-29)**  \n"
    "- **User local time:** 9:02 PM (Europe/Berlin, UTC+01:00)  \n"
    "- **Current UTC time:** 20:02"
)
RAW_DATE_BLOCK = (
    "## Current date and time\n"
    "Today is Thursday, October 29, 2026 (2026-10-29). User local time is 8:46 PM "
    "(Europe/Berlin, UTC+01:00); current UTC time is 19:46.\n"
    "Tomorrow is Friday, October 30, 2026, in the user's local timezone."
)


@pytest.mark.parametrize("echo", [DATE_ECHO, RAW_DATE_BLOCK])
def test_content_that_only_restates_the_date_context_uses_the_previous_answer(env, echo):
    result = _run(json.dumps({
        "title": "Current Date and Time Context", "filename": "current_context.pdf",
        "content_markdown": echo,
    }))

    assert result["exit_code"] == 0
    assert result["attachment"]["name"] == "Steps.pdf"
    _, text = _pdf_text(env, result["attachment"])
    assert "bootrec /rebuildbcd" in text
    assert "UTC" not in text


def test_date_context_inside_real_content_is_removed(env):
    result = _run(json.dumps({
        "title": "Boot fix",
        "content_markdown": RAW_DATE_BLOCK + "\n\n## Steps\n\n1. Boot from the USB stick and open a prompt.",
    }))

    assert result["exit_code"] == 0
    _, text = _pdf_text(env, result["attachment"])
    assert "Boot from the USB stick" in text
    assert "UTC" not in text and "Tomorrow is" not in text


def test_previous_answer_skips_the_approval_prompt_of_the_current_turn(env):
    env.history.append(_msg("assistant", "Allow this task to continue?"))

    result = _run(json.dumps({"title": "Boot fix", "content_markdown": ""}))

    assert result["exit_code"] == 0
    _, text = _pdf_text(env, result["attachment"])
    assert "bootrec /rebuildbcd" in text


def test_no_pdf_when_the_reply_before_the_request_is_not_an_answer(env):
    """The 2026-10-29 21:02 session: a refusal, then "convert this now", then approval."""
    env.history[:] = [
        _msg("user", "can you convert that into pdf"),
        _msg("assistant", "Once you share the content, I'll guide you through exporting it as a PDF."),
        _msg("user", "make an explanation about how to install a game and turn it into a pdf"),
        _msg("assistant", "I can’t perform this request with the currently permitted tools "
                          "(unavailable: capability:pdf_export). I haven’t substituted another tool."),
        _msg("user", "can you convert this now into a pdf"),
        _msg("assistant", "Allow this task to continue?"),
    ]

    result = _run(json.dumps({"title": "Current Date and Time Context", "content_markdown": DATE_ECHO}))

    assert result["exit_code"] == 1
    assert "write the content" in result["error"].lower()
    assert env.handler._load_upload_index() == {}


def test_a_question_reply_is_not_used_as_pdf_content(env):
    env.history[:] = [
        _msg("user", "make a pdf"),
        _msg("assistant", "Sure, which text should go in the PDF?"),
        _msg("user", "the one from before, as a pdf"),
    ]

    result = _run(json.dumps({"title": "T", "content_markdown": ""}))

    assert result["exit_code"] == 1
    assert env.handler._load_upload_index() == {}


def test_plain_text_content_is_treated_as_markdown(env):
    result = _run("# Shopping\n\n- milk\n- bread")

    assert result["exit_code"] == 0
    _, text = _pdf_text(env, result["attachment"])
    assert "milk" in text and "bread" in text


def test_filename_is_sanitized_and_gets_a_pdf_extension(env):
    result = _run(json.dumps({"title": "T", "content_markdown": "x", "filename": "../../etc/my notes"}))

    assert result["exit_code"] == 0
    assert result["attachment"]["name"] == "my_notes.pdf"


def test_without_content_or_previous_answer_it_reports_an_error(env):
    result = _run(json.dumps({"title": "T"}), session_id="missing")

    assert result["exit_code"] == 1
    assert "attachment" not in result
    assert env.handler._load_upload_index() == {}


def test_render_errors_are_reported_without_storing_anything(env):
    from src.pdf_export import MAX_MARKDOWN_CHARS

    result = _run(json.dumps({"title": "T", "content_markdown": "x" * (MAX_MARKDOWN_CHARS + 1)}))

    assert result["exit_code"] == 1
    assert "limit" in result["error"]
    assert env.handler._load_upload_index() == {}


def test_model_sees_the_confirmation_but_not_the_file_reference(env):
    from src.tool_execution import format_tool_result

    result = _run(json.dumps({"title": "Boot fix", "content_markdown": "x"}))
    shown = format_tool_result("create_pdf", result)

    assert "Created the PDF Boot_fix.pdf" in shown
    assert result["attachment"]["id"] not in shown


def test_create_pdf_is_registered_as_a_private_write_tool():
    from src.agent_tools import TOOL_HANDLERS
    from src.tool_capabilities import ToolEffect, capabilities_for_action
    from src.tool_index import BUILTIN_TOOL_DESCRIPTIONS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS
    from src.tool_types import TOOL_TAGS

    assert "create_pdf" in TOOL_TAGS and "create_pdf" in TOOL_HANDLERS
    assert "create_pdf" in BUILTIN_TOOL_DESCRIPTIONS
    schema = next(s for s in FUNCTION_TOOL_SCHEMAS if s["function"]["name"] == "create_pdf")
    assert schema["function"]["parameters"]["required"] == ["title"]
    assert ToolEffect.WRITE_PRIVATE in capabilities_for_action("create_pdf", "{}").effects
