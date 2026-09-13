"""Static regressions for the Agents dashboard's live-update behavior."""

from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
AGENTS = (ROOT / "static/js/agentsDashboard.js").read_text(encoding="utf-8")
WORKBENCH = (ROOT / "static/js/workbench.js").read_text(encoding="utf-8")


def test_background_refresh_updates_regions_without_replacing_dashboard_shell():
    refresh = AGENTS.split("async function refresh()", 1)[1].split("function connect()", 1)[0]
    assert "if (state.open) updateOpenView();" in refresh
    assert "if (state.open) render();" not in refresh
    assert "function updateOpenView()" in AGENTS


def test_duration_tick_does_not_re_render_dashboard():
    tick = AGENTS.split("if (!state.tick)", 1)[1].split("export function close()", 1)[0]
    assert "data-started" in tick
    assert "render();" not in tick


def test_live_refresh_preserves_composer_draft_and_focus():
    detail = AGENTS.split("function renderDetail()", 1)[1].split("function approvalHtml", 1)[0]
    assert "draft[el.id] = el.value" in detail
    assert "focus({ preventScroll: true })" in detail
    assert "setSelectionRange" in detail


def test_workers_can_be_opened_in_workbench_for_inspection():
    assert 'data-ag="inspect-run"' in AGENTS
    assert "export async function openRun(runId, sessionId)" in WORKBENCH
    assert "openRun" in WORKBENCH.rsplit("export default", 1)[1]
