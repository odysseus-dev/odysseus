from pathlib import Path
import subprocess

from tests.helpers.stylesheets import app_css
from tests.helpers.js_modules import email_library_source


ROOT = Path(__file__).resolve().parents[1]


def test_shared_action_menu_order_is_used_by_item_menus() -> None:
    expected_imports = {
        "static/js/documentLibrary.js": "orderActionMenuItems",
        "static/js/tasks.js": "orderActionMenuItems",
        "static/js/sessions.js": "orderActionMenuItems",
        "static/js/research/panel.js": "orderActionMenuItems",
        "static/js/memory.js": "orderActionMenuItems",
    }
    for relative_path, helper in expected_imports.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "actionMenuOrder.js" in source
        assert helper in source
    # The email library is a package, so the import and the call can sit in
    # different modules of it.
    email = email_library_source()
    assert "actionMenuOrder.js" in email
    assert "orderActionMenuItems" in email


def test_common_action_order_matches_product_convention() -> None:
    source = (ROOT / "static/js/actionMenuOrder.js").read_text(encoding="utf-8")
    for rank in (200, 400, 500, 550, 600, 650, 700, 900, 1000):
        assert f"{{ rank: {rank}" in source


def test_callback_actions_are_sorted_by_their_labels() -> None:
    script = """
      import { orderActionMenuItems } from './static/js/actionMenuOrder.js';
      const callback = () => {};
      const items = [
        { label: 'Delete', action: callback },
        { label: 'Archive', action: callback },
        { label: 'Copy', action: callback },
        { label: 'Favorite', action: callback },
        { label: 'Select', action: callback },
        { label: 'Rename', action: callback },
        { label: 'Open', action: callback },
        { label: 'Cancel', action: callback },
      ];
      process.stdout.write(orderActionMenuItems(items).map(item => item.label).join('|'));
    """
    result = subprocess.run(
        ["node", "--input-type=module", "--eval", script],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout == "Open|Rename|Favorite|Copy|Select|Archive|Delete|Cancel"


def test_action_order_module_is_precached() -> None:
    service_worker = (ROOT / "static/sw.js").read_text(encoding="utf-8")
    assert "'/static/js/actionMenuOrder.js'" in service_worker


def test_dropdown_select_actions_use_the_canonical_icon() -> None:
    source = (ROOT / "static/js/actionMenuOrder.js").read_text(encoding="utf-8")
    assert "export const SELECT_MENU_ICON" in source
    for relative_path in (
        "static/js/documentLibrary.js",
        "static/js/memory.js",
        "static/js/sessions.js",
        "static/js/skills.js",
        "static/js/tasks.js",
        "static/js/research/panel.js",
    ):
        module = (ROOT / relative_path).read_text(encoding="utf-8")
        assert "SELECT_MENU_ICON" in module
    assert "SELECT_MENU_ICON" in email_library_source()


def test_email_filter_menu_has_context_title() -> None:
    source = email_library_source()
    assert 'email-filter-menu-title">Filter by...</div>' in source


def test_email_setting_toggles_render_neutral_disabled_state() -> None:
    source = email_library_source()
    style = app_css()
    assert 'email-settings-auto-reply-section' in source
    assert 'email-settings-display-enabled-state' in source
    assert 'stateLabel = section?.querySelector' in source
    assert '.email-settings-section.is-disabled .email-settings-enabled-state' in style


def test_email_search_options_menu_has_context_title() -> None:
    source = email_library_source()
    menu_start = source.index('id="email-search-options-menu"')
    menu_end = source.index("</div>", menu_start) + len("</div>")
    assert 'email-search-options-title">Filter by...</div>' in source[menu_start:menu_end]


def test_email_date_headers_mark_unexpected_timeline_gaps() -> None:
    source = email_library_source()
    assert "function _emailTimelineGapThreshold(items)" in source
    assert "email-date-gap-break" in source
    assert "gapDays > 90 && gapDays > timelineGapThreshold" in source

    style = app_css()
    assert ".date-section-header.email-date-gap-break" in style


def test_email_filters_and_card_favorite_toggle_are_wired() -> None:
    source = email_library_source()
    assert '<option value="tag:action-needed">' not in source
    assert "filter:tag:action-needed" not in source
    assert "email-card-favorite" in source
    assert "aria-pressed" in source
    assert "/api/email/flag/" in source
    assert "Object.prototype.hasOwnProperty.call(em, 'is_flagged')" in source
    assert "const favoritesView = state._libFilter === 'favorites';" in source
    assert "statusCluster.insertBefore(favoriteToggle, doneControl)" in source
    assert "statusCluster.className = 'email-card-status';" in source
    assert "statusCluster.appendChild(att)" in source
    assert "statusCluster.appendChild(doneCheck)" in source
    assert "function _exactTypedFilterSuggestion(value)" in source
    assert "opt.value === 'filter:has-attachments'" in source
    assert "const typedFilter = _exactTypedFilterSuggestion(v);" in source
    assert "_acceptSuggestion(typedFilter);" in source

    style = app_css()
    favorite_start = style.index(".email-card-favorite {")
    favorite_end = style.index("}", favorite_start) + 1
    assert "top: -3px;" in style[favorite_start:favorite_end]
    assert ".email-card-status" in style


def test_email_auto_reply_start_date_seeds_today_when_picker_opens() -> None:
    source = email_library_source()
    assert "function _todayDateInputValue()" in source
    assert "if (autoReplyStart && !autoReplyStart.value) autoReplyStart.value = _todayDateInputValue();" in source
    assert "autoReplyStart?.addEventListener('pointerdown', seedAutoReplyStartDate);" in source
    assert "autoReplyStart?.addEventListener('focus', seedAutoReplyStartDate);" in source


def test_email_auto_reply_syncs_one_calendar_event_per_account() -> None:
    source = email_library_source()
    assert "function _syncAutoReplyCalendarEvent(cfg)" in source
    assert "summary: 'Email Auto Reply (away)'" in source
    assert "function _findAutoReplyCalendarEventUids(cfg, accountId)" in source
    assert "Odysseus email auto reply - account:" in source
    assert "_AUTO_REPLY_CALENDAR_KEY_PREFIX" in source
    assert "method: 'POST', body: JSON.stringify(payload)" in source
    assert "method: 'PUT', body: JSON.stringify(payload)" in source
    assert "method: 'DELETE'" in source
    assert "all_day: true" in source
    assert "await _syncAutoReplyCalendarEvent(savedCfg)" in source
    assert "_syncAutoReplyCalendarEvent(cfg).catch" in source


def test_email_settings_show_away_account_and_compact_display_controls() -> None:
    source = email_library_source()
    style = app_css()
    assert 'email-account-away-label">(AWAY)</span>' in source
    assert 'id="email-lib-auto-reply-badge"' in source
    assert ">Show Email Tags</span>" in source
    assert "enabled ? 'Show' : 'Hide'" in source
    assert "email-settings-inline-link" in source
    assert "email-auto-reply-exclude" not in source
    assert "_emailWritingStyleHtml(writingStyle) + _emailDisplaySettingsHtml(cfg)" in source
    assert ".email-settings-status.is-success" in style
    assert "var(--color-success, #4caf50)" in style
    assert ".email-style-settings-extract svg" in style
    assert "export async function mountEmailSettings(host)" in source
    assert "_openGlobalEmailSettings('show-tags')" in source
    assert source.count('class="admin-card email-settings-section') == 5
    assert 'id="settings-email-default-card"' in (ROOT / "static/index.html").read_text(encoding="utf-8")
    assert "multipleAccounts" in source


def test_email_cleanup_uses_the_memory_tidy_star_icon() -> None:
    source = email_library_source()
    cleanup = source[source.index("function _emailCleanupSettingsHtml"):source.index("function _emailDisplaySettingsHtml")]
    assert "email-settings-clean-btn" in cleanup
    assert "M12 0L14.59 8.41L23 12L14.59 15.59L12 24L9.41 15.59L1 12L9.41 8.41Z" in cleanup


def test_email_settings_escape_returns_to_email_list() -> None:
    source = email_library_source()
    settings_guard = "if (modal.classList.contains('email-settings-mode'))"
    assert settings_guard in source
    assert source.index(settings_guard) < source.index("closeEmailLibrary();", source.index(settings_guard))
    assert "_hideEmailSettingsPage();" in source[source.index(settings_guard):source.index(settings_guard) + 180]


def test_email_select_escape_cancels_selection_without_closing_library() -> None:
    source = email_library_source()
    select_guard = "if (state._selectMode) {"
    select_start = source.index(select_guard, source.index("if (e.key === 'Escape')"))
    assert "_setSelectBtnState(false);" in source[select_start:select_start + 260]
    assert "closeEmailLibrary();" not in source[select_start:select_start + 260]


def test_chat_delete_actions_use_the_shared_trash_bin_icon() -> None:
    source = (ROOT / "static/js/chatRenderer.js").read_text(encoding="utf-8")
    assert "const TRASH_ICON =" in source
    assert "{ id: 'delete', icon: TRASH_ICON" in source
    assert source.count("{ id: 'delete', icon: TRASH_ICON") == 2
    assert "M3 6h18" in source


def test_agent_unsubscribe_uses_the_reviewed_target_without_rescanning() -> None:
    source = email_library_source()
    start = source.index("function _askAgentToUnsubscribe")
    end = source.index("function _unsubscribeCandidateUids", start)
    prompt = source[start:end]
    assert "private_browser" in prompt
    assert "Do not call scan_email_unsubscribes again" in prompt
    assert "Reviewed method_index" in prompt
    assert "Exact unsubscribe URL" in prompt
    assert "bulk_email action=delete" in prompt
    assert "Email UID(s)" in prompt


def test_email_clean_always_forces_a_fresh_unsubscribe_scan() -> None:
    source = email_library_source()
    start = source.index("function _bindEmailSettingsPageControls")
    end = source.index("function _setUnsubButtonBusy", start)
    controls = source[start:end]
    assert "_openUnsubscribeReviewModal(ev.currentTarget, { forceRescan: true })" in controls
    assert "statusEl.style.justifyContent = 'flex-end'" in source


def test_unsubscribe_duplicate_badge_is_lowered() -> None:
    frontend = email_library_source()
    stylesheet = app_css()
    assert "email-unsub-duplicate-badge" in frontend
    start = stylesheet.index(".email-unsub-duplicate-badge {")
    assert "top: 2px;" in stylesheet[start:stylesheet.index("}", start) + 1]


def test_unsubscribe_scan_status_sits_before_clean_action() -> None:
    frontend = email_library_source()
    stylesheet = app_css()
    start = frontend.index("function _emailCleanupSettingsHtml")
    end = frontend.index("function _emailDisplaySettingsHtml", start)
    cleanup = frontend[start:end]
    assert "email-settings-clean-actions" in cleanup
    assert cleanup.index("email-settings-clean-status") < cleanup.index("email-settings-clean-btn")
    assert "inlineHost.querySelector('.email-settings-clean-status')" in frontend
    css_start = stylesheet.index(".email-settings-clean-status {")
    assert "width: auto !important;" in stylesheet[css_start:stylesheet.index("}", css_start) + 1]
    assert "email-unsub-panel-status" in frontend
    assert "modal.style.cssText = 'display:none;margin-top:10px;'" in frontend
    assert "showFinalStatus(finalStatus)" in frontend
    assert "email-unsub-delete-all-btn').style.display = candidates.length ? 'inline-flex' : 'none'" in frontend
    assert "statusEl.classList.add('is-busy')" in frontend
    assert ".email-settings-clean-actions:has(.email-settings-clean-status.is-busy)" in stylesheet
    panel_css = stylesheet[stylesheet.index(".email-unsub-panel-status {"):]
    assert "top: 2px;" in panel_css[:panel_css.index("}") + 1]


def test_unsubscribe_success_removes_messages_before_the_next_scan() -> None:
    frontend = email_library_source()
    backend = (ROOT / "routes/email/email_routes.py").read_text(encoding="utf-8")
    mcp = (ROOT / "mcp_servers/email_server.py").read_text(encoding="utf-8")
    assert "async function _deleteAfterUnsubscribe" in frontend
    assert "action: 'delete'" in frontend[frontend.index("async function _deleteAfterUnsubscribe"):]
    execute = backend[backend.index('@router.post("/unsubscribe/execute")'):]
    assert 'deleted = _move_email_message(conn, uid, "Trash", role="trash")' in execute
    assert '"deleted": deleted' in execute
    unsubscribe = mcp[mcp.index("def _unsubscribe_email"):mcp.index("def _extract_text", mcp.index("def _unsubscribe_email"))]
    assert "_delete_email(uid, folder=folder, account=account)" in unsubscribe
    assert '"deleted": deleted' in unsubscribe


def test_agent_email_mutations_reconcile_bulk_single_and_mailto_results() -> None:
    source = email_library_source()
    start = source.index("function _agentDeletedEmailUids")
    end = source.index("function _handleAgentEmailToolOutput", start)
    resolver = source[start:end]
    assert "tool.includes('bulk_email')" in resolver
    assert "tool.endsWith('delete_email')" in resolver
    assert "source email moved to trash" in resolver
    assert "data.uid" in resolver


def test_browser_agent_unsubscribe_cleans_sender_after_positive_confirmation() -> None:
    source = email_library_source()
    start = source.index("function _agentBrowserUnsubscribeSucceeded")
    end = source.index("function _agentDeletedEmailUids", start)
    browser_flow = source[start:end]
    assert "already\\s+unsubscribed" in browser_flow
    assert "_deleteAfterUnsubscribe([candidate])" in browser_flow
    assert "scope: group.sender ? 'sender_unsubscribe'" in source
    assert "cleanupInFlight" in browser_flow


def test_auto_unsubscribe_all_is_visibly_taller_than_toolbar_buttons() -> None:
    source = app_css()
    start = source.index(".email-unsub-auto-safe-btn {")
    assert "height: 29px;" in source[start:source.index("}", start) + 1]


def test_email_mutation_tool_events_include_exact_arguments() -> None:
    source = (ROOT / "src/agent_loop.py").read_text(encoding="utf-8")
    start = source.index("# Emit tool_output (include ui_event data if present)")
    end = source.index("if tool_call_id:", start)
    event = source[start:end]
    assert '"mcp__email__bulk_email"' in event
    assert '"mcp__email__delete_email"' in event
    assert '"mcp__email__unsubscribe_email"' in event
    assert '"private_browser"' in event
    assert 'tool_output_data["tool_args"]' in event


def test_unsubscribe_cleanup_can_remove_same_sender_unsubscribe_messages() -> None:
    source = (ROOT / "routes" / "email" / "email_routes.py").read_text()
    cleanup = source[source.index('@router.post("/unsubscribe/cleanup")'):source.index('@router.get("/contacts")')]
    assert 'scope == "sender_unsubscribe"' in cleanup
    assert "_unsubscribe_sender_uids_sync" in cleanup
    sender_scan = source[source.index("def _unsubscribe_sender_uids_sync"):source.index('@router.get("/unsubscribe/scan")')]
    assert "FROM {_imap_search_quote(sender_key)}" in sender_scan
    assert 'candidate.get("from_address")' in source[source.index("def _unsubscribe_sender_uids_sync"):source.index('@router.get("/unsubscribe/scan")')]


def test_unsubscribe_review_marks_handled_cards_and_offers_scan_further() -> None:
    source = email_library_source()
    start = source.index("function _markUnsubscribeCardDone")
    end = source.index("async function _runUnsubscribeCleanup", start)
    card = source[start:end]
    assert "_UNSUB_CHECK_ICON" in card
    assert "is-unsubscribed" in card
    assert "email-unsub-scan-further" in source


def test_unsubscribe_review_can_ignore_a_candidate_without_deleting_it() -> None:
    source = email_library_source()
    styles = app_css()
    assert "email-unsub-ignore-btn" in source
    assert "_rememberUnsubscribeIgnored(c)" in source
    assert "Ignore this unsubscribe candidate" in source
    assert ".email-unsub-ignore-btn" in styles


def test_email_settings_sections_use_static_headers() -> None:
    source = email_library_source()
    styles = app_css()
    assert 'class="email-unsub-accent-icon"' in source
    assert 'M12 0L14.59 8.41' in source
    assert 'Scanning ${_esc(scanFolderLabel)} headers…' in source
    assert 'email-settings-clean-btn' in source
    assert 'const inlineHost = settingsPage?.querySelector?.(\'.email-settings-cleanup-section\')' in source
    assert '<div class="admin-card email-settings-section' in source
    assert 'class="email-settings-section-head"' in source
    assert '<div id="email-settings-cleanup-body" class="email-settings-section-body">' in source
    assert '<details class="admin-card email-settings-section' not in source
    assert source.count('id="email-auto-reply-enabled"') == 1
    assert source.count('id="email-settings-show-tags"') == 1
    assert 'flex: 0 0 auto;' in styles
    assert 'page.querySelectorAll(\'details.email-settings-section\')' not in source
    assert 'other.open = false' not in source
    assert '.email-settings-section[open] > .email-settings-section-body' in styles
    assert '.email-settings-section[open] > .email-settings-section-body > *' in styles
    assert 'flex: 0 0 auto;' in styles
    assert '.email-settings-section[open] {' in styles
    assert 'flex: 1 1 auto;' in styles
    assert 'grid-template-rows: auto minmax(0, 1fr);' in styles
    assert 'height: 100%;' in styles
    assert 'max-height: 100%;' in styles
    assert 'overflow: hidden;' in styles
    assert '.modal-content:not([style*="height"])' in styles
    assert 'left: -1px;' in styles
    assert '.email-settings-clean-status:not(:empty)' in styles
    assert '.email-unsub-status.is-error' in styles


def test_unsubscribe_scan_defaults_to_bounded_page_in_api_and_tool_prompt() -> None:
    backend = (ROOT / "routes" / "email" / "email_routes.py").read_text()
    schema = (ROOT / "src" / "tool_schemas.py").read_text()
    agent = (ROOT / "src" / "agent_loop.py").read_text()
    scan_start = backend.index('@router.get("/unsubscribe/scan")')
    scan_end = backend.index('@router.post("/unsubscribe/execute")', scan_start)
    scan_route = backend[scan_start:scan_end]
    assert 'max_scan: int = Query(500)' in scan_route
    assert 'max_scan = max(limit, min(requested_max_scan or 500, 500))' in backend
    assert 'for start in range(0, len(uids), 100)' in backend
    assert 'capped at 500' in schema
    assert '"max_scan": 500' in agent[agent.index('def _parse_qwen_explicit_unsubscribe_scan_request'):agent.index('def _parse_qwen_explicit_unsubscribe_email_request')]
    mcp = (ROOT / "mcp_servers" / "email_server.py").read_text()
    mcp_scan = mcp[mcp.index('def _scan_unsubscribe_candidates'):mcp.index('def _unsubscribe_email')]
    assert 'max_scan=500' in mcp_scan
    assert 'for start in range(0, len(uids), 100)' in mcp_scan
    assert 'capped at 500' in mcp
    assert 'Scan up to 500 newest email headers' in mcp


def test_item_menus_share_the_standard_dropdown_classes() -> None:
    expected = {
        "static/js/documentLibrary.js": "dropdown session-dropdown-menu doclib-card-dropdown",
        "static/js/memory.js": "dropdown session-dropdown-menu memory-item-dropdown",
        "static/js/tasks.js": "dropdown session-dropdown-menu task-dropdown",
        "static/js/skills.js": "dropdown session-dropdown-menu skill-kebab-menu",
    }
    for relative_path, class_names in expected.items():
        source = (ROOT / relative_path).read_text(encoding="utf-8")
        assert class_names in source


def test_task_card_menu_can_enter_select_mode_with_current_task() -> None:
    source = (ROOT / "static/js/tasks.js").read_text(encoding="utf-8")
    assert "function _taskEnterSelectWith(taskId)" in source
    assert "label: 'Select'" in source
    assert "action: () => _taskEnterSelectWith(task.id)" in source
