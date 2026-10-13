from pathlib import Path
import re
from tests.helpers.stylesheets import app_css


def test_compare_renders_ask_user_in_the_originating_pane():
    root = Path(__file__).resolve().parents[1]
    stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")

    assert "renderAskUserCard" in stream
    assert "} else if (json.type === 'ask_user') {" in stream
    assert "root: hist" in stream
    assert "state._paneSessionIds[paneIdx] === sessionId" in stream
    assert "streamToPane(paneIdx, sessionId, message, aiMessage, resumeOptions)" in stream
    assert "handleCompareSubmit" not in stream


def test_compare_submits_approval_only_to_the_pane_session():
    root = Path(__file__).resolve().parents[1]
    stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")

    assert "fd.append('tool_approval_id', opts.toolApproval.approval_id || '');" in stream
    assert "fd.append('tool_approval_decision', opts.toolApproval.decision || '');" in stream
    assert "const isApproval = submission.kind === 'tool_approval';" in stream
    assert "const message = isApproval ? ''" in stream
    assert "if (!isApproval) _appendPaneMessage(hist, 'user', message);" in stream
    assert "json.type === 'tool_approval_resolved'" in stream
    assert "json.decision === 'deny' ? 'Denied.'" in stream


def test_compare_continuation_does_not_lose_or_replace_pane_ownership():
    root = Path(__file__).resolve().parents[1]
    stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")
    index = (root / "static/js/compare/index.js").read_text(encoding="utf-8")

    assert "if (state._abortControllers[paneIdx] === ac)" in stream
    assert "if (activeController === originController)" in stream
    assert "if (activeController) return;" in stream
    assert "registerStreamActions({ rerollPane, autoPreviewHtml: _autoPreviewHtml, setSendBtn: _setSendBtn });" in index
    assert "const compareStillStreaming = state._abortControllers.some(Boolean);" in index
    assert "_setSendBtn(compareStillStreaming ? 'stop' : 'send');" in index


def test_compare_agent_harness_matches_current_compare_payload():
    root = Path(__file__).resolve().parents[1]
    stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")

    assert "const isAgent = state._compareMode === 'agent';" in stream
    agent_start = stream.index("if (isAgent) {")
    research_start = stream.index("} else if (isResearch)", agent_start)
    agent_block = stream[agent_start:research_start]
    common_start = stream.index("// Disable document tool and memory injection in compare mode", research_start)
    common_block = stream[common_start:stream.index("const response = await fetch", common_start)]

    assert "fd.append('mode', 'agent');" in agent_block
    assert "fd.append('allow_web_search', 'true');" in agent_block
    assert "fd.append('allow_bash', 'true');" in agent_block
    assert "fd.append('use_rag', 'false');" not in agent_block
    assert "fd.append('no_documents', 'true');" in common_block
    assert "fd.append('no_memory', 'true');" in common_block
    assert "fd.append('compare_mode', 'true');" in common_block
    assert "function _compareTimezoneHeaders()" in stream
    assert "'X-Tz-Offset': String(-new Date().getTimezoneOffset())" in stream
    assert "Intl.DateTimeFormat().resolvedOptions().timeZone" in stream
    assert "headers: _compareTimezoneHeaders()" in stream


def test_compare_uses_current_chat_renderer_for_ask_user_cards():
    root = Path(__file__).resolve().parents[1]
    stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")
    vote = (root / "static/js/compare/vote.js").read_text(encoding="utf-8")

    stream_version = re.search(r"\.\./chatRenderer\.js\?v=([A-Za-z0-9_-]+)", stream)
    vote_version = re.search(r"\.\./chatRenderer\.js\?v=([A-Za-z0-9_-]+)", vote)
    assert stream_version, "Compare stream must import the shared chat renderer"
    assert vote_version, "Compare vote must import the shared chat renderer"
    assert stream_version.group(1) == vote_version.group(1)


def test_compare_pane_templates_hide_response_actions_until_response_exists():
    root = Path(__file__).resolve().parents[1]
    index = (root / "static/js/compare/index.js").read_text(encoding="utf-8")
    panes = (root / "static/js/compare/panes.js").read_text(encoding="utf-8")
    styles = app_css()

    assert re.search(r"from './panes\.js\?v=[A-Za-z0-9_-]+'", index)
    assert re.search(r"from './selector\.js\?v=[A-Za-z0-9_-]+'", index)
    assert index.count('pane-action-btn pane-stop-btn') >= 1
    assert index.count('pane-action-btn pane-needs-response" data-action="reroll"') >= 1
    assert index.count('pane-action-btn pane-needs-response" data-action="copy"') >= 1
    assert panes.count('pane-action-btn pane-stop-btn') >= 2
    assert panes.count('pane-action-btn pane-needs-response" data-action="reroll"') >= 2
    assert panes.count('pane-action-btn pane-needs-response" data-action="copy"') >= 2
    assert "function _paneModeBadgeHtml(paneIdx)" in index
    assert "function _paneModeBadgeHtml(paneIdx)" in panes
    assert 'pane-mode-badge pane-mode-' in index
    assert panes.count('pane-mode-badge pane-mode-') >= 1
    assert ".pane-mode-badge" in styles
    assert ".pane-mode-agent" in styles
    assert ".pane-mode-search" in styles
    assert ".pane-mode-research" in styles

    add_start = panes.index("async function _createAndAppendPane")
    add_end = panes.index("// Append to grid", add_start)
    add_template = panes[add_start:add_end]
    assert 'pane-action-btn pane-stop-btn' in add_template
    assert 'pane-action-btn pane-needs-response" data-action="reroll"' in add_template
    assert 'pane-action-btn pane-needs-response" data-action="copy"' in add_template


def test_compare_model_swap_creates_replacement_before_mutating_current_pane():
    root = Path(__file__).resolve().parents[1]
    panes = (root / "static/js/compare/panes.js").read_text(encoding="utf-8")

    swap_start = panes.index("item.addEventListener('click', async (e) => {")
    swap_end = panes.index("// Update title display", swap_start)
    swap = panes[swap_start:swap_end]

    create_idx = swap.index("const res = await fetch(`${state.API_BASE}/api/session`")
    ok_idx = swap.index("if (!res.ok) throw new Error('HTTP ' + res.status);")
    id_idx = swap.index("newSessionId = data.id || '';")
    state_idx = swap.index("state._selectedModels[paneIdx] = {")
    delete_idx = swap.index("fetch(`${state.API_BASE}/api/session/${oldSid}`")

    assert create_idx < ok_idx < id_idx < state_idx < delete_idx
    assert "if (uiModule?.showError) uiModule.showError('Failed to swap compare model:" in swap
    assert "return;" in swap[swap.index("catch (err)") : state_idx]


def test_compare_restores_the_card_instead_of_dropping_a_timed_out_choice():
    """The card is removed the moment a choice is accepted.

    If the originating stream still owns the pane when the resume deadline
    passes, the decision has nowhere to go — so the card has to come back
    rather than the click vanishing silently.
    """

    root = Path(__file__).resolve().parents[1]
    stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")

    assert "function _restorePaneAskUserCard(" in stream
    assert "_restorePaneAskUserCard(paneIdx, sessionId, submission, originController);" in stream
    assert "submission.payload || {}" in stream

    start = stream.index("function _resumePaneChoiceWhenIdle(")
    end = stream.index("function _renderPaneAskUserCard(", start)
    resume = stream[start:end]
    # The deadline must not fall through to a bare return any more.
    assert "if (Date.now() - startedAt < 10000) setTimeout(resume, 25);" not in resume


def test_compare_panes_have_visible_runtime_state_without_revealing_empty_actions():
    root = Path(__file__).resolve().parents[1]
    stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")
    panes = (root / "static/js/compare/panes.js").read_text(encoding="utf-8")
    styles = app_css()

    assert "_paneEl.classList.remove('is-done', 'is-failed', 'is-awaiting-input');" in stream
    assert "_paneEl.classList.add('is-streaming');" in stream
    assert "paneEl.classList.add('is-awaiting-input');" in stream
    assert "_paneElFinal.classList.remove('is-streaming');" in stream
    assert "_paneElFinal.classList.toggle('is-done', streamOk && !awaitingChoice);" in stream
    assert "_paneElFinal.classList.toggle('is-failed', !streamOk && !awaitingChoice);" in stream
    assert "if (paneEl && !awaitingChoice && accumulated.trim())" in stream
    assert "pane.classList.remove('is-streaming', 'is-awaiting-input');" in panes
    assert "pane.classList.remove('is-streaming', 'is-awaiting-input', 'is-done');" in panes
    assert "pane.classList.add('is-failed');" in panes
    assert "panes[i].classList.remove('winner', 'loser', 'is-streaming', 'is-awaiting-input', 'is-done', 'is-failed');" in panes

    assert ".compare-pane.is-streaming" in styles
    assert ".compare-pane.is-streaming::before" in styles
    assert "@keyframes compare-pane-progress" in styles
    assert ".compare-pane.is-awaiting-input" in styles
    assert ".compare-pane.is-done" in styles
    assert ".compare-pane.is-failed" in styles


def test_compare_panes_surface_compact_result_summary():
    root = Path(__file__).resolve().parents[1]
    index = (root / "static/js/compare/index.js").read_text(encoding="utf-8")
    panes = (root / "static/js/compare/panes.js").read_text(encoding="utf-8")
    stream = (root / "static/js/compare/stream.js").read_text(encoding="utf-8")
    styles = app_css()

    assert 'pane-header-row pane-header-secondary' in index
    assert 'class=\"pane-summary\" id=\"cmp-summary-' in index
    assert panes.count('pane-summary" id="cmp-summary-') >= 2
    assert "function _compactNumber(value)" in stream
    assert "function _formatCost(value)" in stream
    assert "function _setPaneSummary(paneIdx, metrics, cost)" in stream
    assert "_setPaneSummary(paneIdx, null, null);" in stream
    assert "_setPaneSummary(paneIdx, metrics, _cost);" in stream
    assert "summary.textContent = bits.join(' · ');" in stream
    assert "finBadge.parentNode.insertBefore(badge, finBadge)" in stream
    assert "header.insertBefore(badge, finBadge)" not in stream
    assert "cmp-summary-' + paneIdx" in panes
    assert "summary.textContent = ''; summary.title = '';" in panes

    assert ".pane-summary" in styles
    assert ".pane-summary:empty { display: none; }" in styles
    assert "font-variant-numeric: tabular-nums;" in styles


def test_compare_selector_surfaces_duplicate_warning_without_blocking_start():
    root = Path(__file__).resolve().parents[1]
    selector = (root / "static/js/compare/selector.js").read_text(encoding="utf-8")
    styles = app_css()

    assert "function _selectionKey(sel)" in selector
    assert "function _duplicateSelectionKeys()" in selector
    assert "function _appendSelectionMeta(row, sel, duplicateKeys)" in selector
    assert "function _updateStartReadiness()" in selector
    assert "row.classList.add('cmp-model-row-duplicate');" in selector
    assert "Duplicate selection" in selector
    assert "startBtn.disabled = false;" in selector
    assert "Duplicate selections will run as separate panes" in selector
    assert "if (selections.length > 1)" in selector
    assert selector.count("renderModelRows();") >= 12

    assert ".cmp-model-row-duplicate" in styles
    assert ".cmp-model-meta" in styles
    assert ".cmp-model-meta-chip" in styles
    assert ".cmp-model-meta-warning" in styles
    assert ".cmp-model-meta-muted" in styles
    meta_block = styles[styles.index(".cmp-model-meta {"):styles.index(".cmp-model-meta-chip,", styles.index(".cmp-model-meta {"))]
    rm_block = styles[styles.index(".cmp-rm-btn {"):styles.index(".cmp-prov-select {", styles.index(".cmp-rm-btn {"))]
    assert "order: 3;" in meta_block
    assert "order: 2;" in rm_block
    assert "margin-left: auto;" in rm_block
    assert "align-self: center;" in rm_block
    assert "top: -2px;" in rm_block


def test_unsaved_compare_helper_sessions_do_not_render_in_sidebar():
    root = Path(__file__).resolve().parents[1]
    sessions = (root / "static/js/sessions.js").read_text(encoding="utf-8")

    assert "function _isUnsavedCompareSession(s)" in sessions
    assert "name.startsWith('[CMP] ') && !folder.startsWith('Compare:')" in sessions
    assert "!_isUnsavedCompareSession(s)" in sessions
    assert "const visibleSessions = sessions.filter(s => !s.archived && !_isUnsavedCompareSession(s));" in sessions
    assert "const activeSessions = sessions.filter(s => !s.archived && !_isUnsavedCompareSession(s));" in sessions
    assert "s.folder === 'Assistant' || s.folder === 'Tasks' || _isUnsavedCompareSession(s)" in sessions
