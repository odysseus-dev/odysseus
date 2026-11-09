from src.tool_index import ToolIndex
from src.agent_loop import _compact_native_route_tools


def empty_index() -> ToolIndex:
    index = ToolIndex.__new__(ToolIndex)
    index.retrieve = lambda _query, k=8: []
    return index


def test_explicit_scheduled_task_concepts_survive_multilingual_retrieval_miss():
    index = empty_index()
    prompts = (
        "检查所有计划任务的运行状态，并修复异常项。",
        "確認所有定時任務的狀態。",
        "定期タスクの状態を確認してください。",
        "예약된 작업 상태를 확인해 주세요.",
    )
    for prompt in prompts:
        assert "manage_tasks" in index.get_tools_for_query(prompt)


def test_other_explicit_state_domains_have_literal_hints():
    index = empty_index()
    assert "manage_calendar" in index.get_tools_for_query("查看我的日历事件")
    assert "manage_notes" in index.get_tools_for_query("创建一个待办清单")


def test_unrelated_non_latin_text_does_not_enable_state_managers():
    tools = empty_index().get_tools_for_query("请概括这段普通文字")
    assert not {"manage_tasks", "manage_calendar", "manage_notes"} & tools


def test_compact_reducer_preserves_explicit_multilingual_task_manager():
    original = {"manage_tasks", "list_emails", "read_email", "send_email", "read_file"}
    tools = _compact_native_route_tools(
        original,
        "检查所有计划任务的运行状态，并发邮件通知团队。",
        {"email"},
    )
    assert "manage_tasks" in tools
