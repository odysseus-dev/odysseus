from pathlib import Path
import re


ROOT = Path(__file__).resolve().parent.parent


def test_tasks_open_defaults_to_completed_with_empty_fallback():
    src = (ROOT / "static/js/tasks.js").read_text()

    assert "let _completedFallbackToTasksOnce = false;" in src
    assert "const openCompletedByDefault = !focusId && o.filter === undefined && !openActivityForFailure;" in src
    assert "_completedFallbackToTasksOnce = openCompletedByDefault && !openCompletedForNotification;" in src
    assert "_switchTab(openActivityForFailure ? 'activity' : openCompletedByDefault ? 'completed' : 'tasks');" in src
    assert "if (_completedFallbackToTasksOnce)" in src
    assert "_switchTab('tasks');" in src


def test_tasks_completed_default_asset_versions_are_bumped():
    app_src = (ROOT / "static/app.js").read_text()
    sw_src = (ROOT / "static/sw.js").read_text()

    app_match = re.search(r"(?:\./js|/static/js)/tasks\.js\?v=([^'\"]+)", app_src)
    sw_match = re.search(r"/static/js/tasks\.js\?v=([^'\"]+)", sw_src)

    assert app_match, "app.js must import a versioned Tasks module"
    assert sw_match, "the service worker must precache the exact versioned Tasks URL"
    assert sw_match.group(1) == app_match.group(1)


def test_tasks_filter_chips_include_active_paused_switch():
    src = (ROOT / "static/js/tasks.js").read_text()

    assert "let _taskStatusFilter = 'active';" in src
    assert "const _taskStatus = (t) => String(t?.status || '').toLowerCase();" in src
    assert "const activeCount = _tasks.filter(t => _taskStatus(t) === 'active').length;" in src
    assert "const pausedCount = _tasks.filter(t => _taskStatus(t) === 'paused').length;" in src
    assert "bar.style.display = _tasks.length ? 'flex' : 'none';" in src
    assert "task-status-filter-chip" in src
    assert "mkChip(`active (${activeCount})`, 'active', _taskStatusFilter === 'active', 'status');" in src
    assert "mkChip(`paused (${pausedCount})`, 'paused', _taskStatusFilter === 'paused', 'status');" in src
    assert "if (_taskStatusFilter && String(t.status || '').toLowerCase() !== _taskStatusFilter) return false;" in src
    assert "_taskStatusFilter = _taskStatusFilter === value ? null : value;" in src
    assert "_taskStatusFilter = null;" in src[src.index("if (kind === 'status')"):src.index("_renderList();", src.index("if (kind === 'status')"))]


def test_tasks_completed_view_exposes_active_paused_shortcuts():
    src = (ROOT / "static/js/tasks.js").read_text()

    assert "function _renderCompletedTaskStatusShortcuts()" in src
    assert 'id="tasks-completed-status-chips"' in src
    assert "_taskStatusFilter = value;" in src
    assert "_switchTab('tasks');" in src
    assert "if (_activeTab === 'completed') _renderCompletedTaskStatusShortcuts();" in src


def test_completed_task_preview_links_research_runs_to_visual_report():
    src = (ROOT / "static/js/tasks.js").read_text()

    assert "entry.researchId" in src
    assert "task-completed-report-btn" in src
    assert "api/research/report/${encodeURIComponent(entry.researchId)}" in src
    assert "task-completed-report-btn').forEach" in src
