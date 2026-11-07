from src.agent_loop import (
    _DOMAIN_TOOL_MAP,
    _classify_agent_request,
    _compact_native_route_tools,
)


def _intent(text: str) -> dict[str, object]:
    return _classify_agent_request([{"role": "user", "content": text}], text)


def test_chinese_email_workflow_selects_email_domain() -> None:
    prompt = "请阅读收件箱里的客户投诉邮件，直接发送符合条件的通知，并把其余回复保存为草稿。"
    intent = _intent(prompt)

    assert intent["low_signal"] is False
    assert "email" in intent["domains"]
    compact = _compact_native_route_tools(
        set(_DOMAIN_TOOL_MAP["email"]),
        prompt,
        {"email"},
    )
    assert compact is not None
    assert {"list_emails", "read_email", "send_email", "draft_email"} <= compact
    assert not {
        "bash", "python", "web_search", "web_fetch", "download_attachment",
        "reply_to_email", "ui_control",
    } & compact


def test_compact_email_surface_adds_only_requested_specialists() -> None:
    available = set(_DOMAIN_TOOL_MAP["email"]) | {
        "bash", "python", "web_search", "web_fetch", "read_file", "ls",
    }
    attachment = _compact_native_route_tools(
        available,
        "Find the email with the quarterly attachment and download it.",
        {"email"},
    )
    reply = _compact_native_route_tools(
        available,
        "Reply to Dana's last email.",
        {"email"},
    )

    assert attachment is not None
    assert {"search_emails", "download_attachment"} <= attachment
    assert "send_email" not in attachment
    assert reply is not None
    assert {"draft_email_reply", "reply_to_email"} <= reply
    assert "download_attachment" not in reply


def test_compact_file_surface_keeps_workspace_discovery_dependency() -> None:
    compact = _compact_native_route_tools(
        {"read_file", "ls", "list_emails", "read_email"},
        "Read the relevant local files and summarize the related email.",
        {"email"},
    )

    assert compact is not None
    assert {"read_file", "ls", "get_workspace"} <= compact


def test_chinese_calendar_and_notes_requests_select_personal_tools() -> None:
    for prompt in (
        "请把下周会议加入日历。",
        "请创建一份待办清单并设置提醒。",
    ):
        intent = _intent(prompt)
        assert intent["low_signal"] is False
        assert "notes_calendar_tasks" in intent["domains"]
