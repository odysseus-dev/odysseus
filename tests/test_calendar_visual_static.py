from pathlib import Path
import re

from tests.helpers.stylesheets import app_css
from tests.helpers.js_modules import email_library_source


ROOT = Path(__file__).resolve().parents[1]


def _occurrences(text, needle):
    """Every index of needle, so an assertion does not depend on which copy of
    a selector the cascade happens to put first."""
    out, i = [], text.find(needle)
    while i != -1:
        out.append(i)
        i = text.find(needle, i + 1)
    return out


def test_calendar_week_view_has_overlap_lanes_and_live_ruler_hooks():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert "function _wkLayoutTimedEvents" in calendar_src
    assert "--lane:${lane};--lane-count:${laneCount}" in calendar_src
    assert "--wk-hour-px:${WEEK_HOUR_PX}px" in calendar_src
    assert "cal-wk-now::after" in style_src
    assert "content:'now'" in style_src
    assert "repeating-linear-gradient(" in style_src
    assert "calc((var(--lane, 0) * (100% / var(--lane-count, 1))) + 2px)" in style_src


def test_crowded_week_events_expand_left_to_reveal_full_title_on_hover():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert "const crowdedClass = laneCount > 1 ? ' cal-wk-block-crowded' : '';" in calendar_src
    assert "--lane-right:${laneRight}%" in calendar_src
    assert "function _layoutCrowdedWeekHover(body)" in calendar_src
    assert "const leftBound = Math.max(columnsRect.left, 0);" in calendar_src
    assert "const rightBound = Math.min(columnsRect.right, window.innerWidth);" in calendar_src
    assert "leftSpace >= desiredWidth || leftSpace >= rightSpace" in calendar_src
    assert "const probe = block.cloneNode(true);" in calendar_src
    assert "const hoverHeight = Math.max(eventHeight, Math.ceil(probe.getBoundingClientRect().height));" in calendar_src
    assert "block.style.setProperty('--hover-height', `${hoverHeight}px`);" in calendar_src
    assert "function _fitWeekBlockVertically(block, body)" in calendar_src
    assert "const topBound = Math.max(wrapRect.top, stickyBottom, viewportTop) + 3;" in calendar_src
    assert "const bottomBound = Math.min(wrapRect.bottom, viewportBottom) - 4;" in calendar_src
    assert "block.style.setProperty('--hover-fit-height'" in calendar_src
    assert "block.style.setProperty('--hover-shift-y'" in calendar_src
    assert "_layoutCrowdedWeekHover(body);" in calendar_src
    hover_idx = style_src.index(".cal-wk-block.cal-wk-block-crowded:hover")
    name_idx = style_src.index(".cal-wk-block.cal-wk-block-crowded:hover .cal-wk-block-name", hover_idx)
    anchor_idx = style_src.index(".cal-wk-block.cal-wk-block-crowded.cal-wk-expand-left")
    assert "left: auto;" in style_src[anchor_idx:hover_idx]
    assert "right: calc(100% - var(--lane-right) + 2px);" in style_src[anchor_idx:hover_idx]
    assert "width: var(--hover-width, 170px);" in style_src[hover_idx:name_idx]
    assert "height: var(--hover-fit-height, var(--hover-height, var(--event-height))) !important;" in style_src[hover_idx:name_idx]
    assert "height 0.2s cubic-bezier" in style_src
    label_idx = style_src.index(".cal-wk-block.cal-wk-block-crowded:hover .cal-wk-block-label", hover_idx)
    assert "position: absolute;" in style_src[label_idx:name_idx]
    assert "width: calc(var(--hover-width, 170px) - 18px);" in style_src[label_idx:name_idx]
    assert ".cal-wk-block.cal-wk-block-crowded.cal-wk-expand-left:hover .cal-wk-block-label" in style_src
    assert ".cal-wk-block.cal-wk-block-crowded.cal-wk-expand-right:hover .cal-wk-block-label" in style_src
    assert "@keyframes cal-wk-hover-label-reveal" in style_src
    assert "z-index: 30;" in style_src[hover_idx:name_idx]
    assert "white-space: normal;" in style_src[name_idx:name_idx + 260]

    next_idx = calendar_src.index("document.getElementById('cal-next')?.addEventListener('click'")
    render_idx = calendar_src.index("_render();", next_idx)
    assert "block.classList.remove('cal-wk-block-crowded');" in calendar_src[next_idx:render_idx]


def test_mobile_truncated_week_event_expands_before_opening_editor():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    click_idx = calendar_src.index("body.querySelectorAll('.cal-wk-block, .cal-wk-allday-event')")
    edit_idx = calendar_src.index("if (ev) _showEventForm(ev);", click_idx)
    expand_idx = calendar_src.index("el.classList.add('cal-wk-mobile-expanded');", click_idx)
    assert "window.matchMedia('(max-width: 768px)').matches" in calendar_src[click_idx:edit_idx]
    assert "name.scrollHeight > name.clientHeight + 1" in calendar_src[click_idx:edit_idx]
    assert expand_idx < edit_idx
    assert "if (titleIsClipped && !el.classList.contains('cal-wk-mobile-expanded'))" in calendar_src
    assert ".cal-wk-block.cal-wk-mobile-expanded" in style_src
    assert "height: var(--hover-fit-height, var(--hover-height, var(--event-height))) !important;" in style_src[
        style_src.index(".cal-wk-block.cal-wk-mobile-expanded"):
    ]


def test_mobile_empty_week_slot_selects_before_opening_new_event():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert "let _selectedWeekSlot = null;" in calendar_src
    assert "function _paintSelectedWeekSlot(body)" in calendar_src
    assert "function _clearSelectedWeekSlot(body)" in calendar_src
    assert "window.matchMedia('(max-width: 768px)').matches && !dragged" in calendar_src
    assert "Math.hypot(mv.clientX - startX, (mv.clientY - rect.top) - startY) > 5" in calendar_src
    same_idx = calendar_src.index("const sameSlot = _selectedWeekSlot?.date === ds")
    select_idx = calendar_src.index("_selectedWeekSlot = { date: ds, start: startHHMM, end: endHHMM };", same_idx)
    open_idx = calendar_src.index("_showEventFormForRange(ds, startHHMM, endHHMM);", select_idx)
    assert same_idx < select_idx < open_idx
    assert "block.classList.remove('cal-wk-mobile-expanded');" in calendar_src[select_idx - 500:select_idx]
    assert ".cal-wk-slot-selected" in style_src
    assert "className = 'cal-wk-slot-resize';" in calendar_src
    assert "resize.addEventListener('pointerdown'" in calendar_src
    assert "_selectedWeekSlot.end = nextEnd;" in calendar_src
    assert ".cal-wk-slot-resize::after" in style_src
    assert ".cal-wk-col.cal-wk-slot-day-selected .cal-wk-col-head" in style_src


def test_week_view_hints_when_current_time_is_below_the_visible_field():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert "function _updateWeekNowBelowHint(wrap)" in calendar_src
    assert "nowRect.top > wrapRect.bottom - 2" in calendar_src
    assert "const todayColumn = nowLine.closest('.cal-wk-col');" in calendar_src
    assert "hint.className = 'cal-wk-now-below-hint';" in calendar_src
    assert "columnRect.left - wrapRect.left + wrap.scrollLeft" in calendar_src
    assert "hint.style.width = `${columnRect.width}px`;" in calendar_src
    assert "requestAnimationFrame(() => _updateWeekNowBelowHint(_wrap));" in calendar_src
    hint_idx = style_src.index(".cal-wk-now-below-hint")
    assert "background: var(--accent, var(--red));" in style_src[hint_idx:hint_idx + 240]


def test_mobile_week_scroll_has_resisted_edge_pull_feedback():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert "pullStartedAtTop = _wrap.scrollTop <= 1;" in calendar_src
    assert "pullStartedAtBottom = _wrap.scrollTop >= maxScroll - 1;" in calendar_src
    assert "const pullingTop = pullStartedAtTop && _wrap.scrollTop <= 1 && dy > 6;" in calendar_src
    assert "const pullingBottom = pullStartedAtBottom && _wrap.scrollTop >= maxScroll - 1 && dy < -6;" in calendar_src
    assert "Math.min(7, Math.abs(dy) * 0.1)" in calendar_src
    assert "_wrap.addEventListener('touchcancel', releaseEdgePull" in calendar_src
    assert ".cal-wk-wrap.cal-wk-edge-release > .cal-wk-cols" in style_src
    assert "transform: translateY(var(--wk-edge-pull, 0));" in style_src
    assert "-webkit-overflow-scrolling: touch;" in style_src


def test_calendar_view_change_recovers_a_fully_open_day_drawer():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()

    view_idx = calendar_src.index("body.querySelectorAll('.cal-view-btn')")
    render_idx = calendar_src.index("_render();", view_idx)
    handler = calendar_src[view_idx:render_idx]
    assert "const drawerWasPinnedOpen" in handler
    assert "calBody.classList.remove('cal-events-full', 'cal-calendar-full');" in handler
    assert "localStorage.removeItem('odysseus.cal.detailH');" in handler
    assert "localStorage.removeItem('odysseus.cal.splitSnap');" in handler
    assert "_selectedDay = (_view === 'month' || _view === 'week') ? _ds(_currentDate) : null;" in handler


def test_calendar_month_and_agenda_have_visual_depth_hooks():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert "cal-weekend" in calendar_src
    assert "cal-empty-day" in calendar_src
    assert ".cal-day.cal-weekend:not(.cal-today)" in style_src
    assert ".cal-day.cal-empty-day::after" in style_src
    assert ".cal-agenda-day::before" in style_src
    assert ".cal-agenda-day::after" in style_src
    assert "cal-event-enter" in style_src


def test_calendar_event_cards_show_compact_source_badges():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert "function _eventSourceHtml" in calendar_src
    assert "${_eventSourceHtml(ev)}" in calendar_src
    assert "${_eventSourceHtml(md)}" in calendar_src
    assert ".cal-event-source" in style_src


def test_calendar_toolbar_previous_next_arrows_are_mobile_only():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert 'class="cal-nav cal-toolbar-arrow" id="cal-prev"' in calendar_src
    assert 'class="cal-nav cal-toolbar-arrow" id="cal-next"' in calendar_src
    base_idx = style_src.index("button.cal-nav.cal-toolbar-arrow {")
    hidden_idx = style_src.index("display: none;", base_idx)
    mobile_idx = style_src.index("@media (max-width: 768px)", hidden_idx)
    shown_idx = style_src.index("button.cal-nav.cal-toolbar-arrow { display: inline-flex !important; }", mobile_idx)
    assert base_idx < hidden_idx < mobile_idx < shown_idx
    assert "#cal-next { margin-left: auto; }" in style_src
    assert "#cal-add { margin-left: auto; }" in style_src


def test_calendar_side_arrows_center_against_calendar_pane():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert "function _alignSideNavToCalendar()" in calendar_src
    assert "paneRect.top - contentRect.top + (paneRect.height / 2)" in calendar_src
    assert "new ResizeObserver(_alignSideNavToCalendar)" in calendar_src
    assert "top: var(--cal-side-nav-y, 50%);" in style_src


def test_calendar_splitter_double_click_snaps_to_nearest_pane_then_toggles():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert "detailH >= calendarH ? 'events' : 'calendar'" in calendar_src
    assert "splitter.addEventListener('dblclick', () => {" in calendar_src
    assert "Date.now() - _lastTouchSnap < 500" in calendar_src
    assert "calBody.classList.contains(eventsFullClass)" in calendar_src
    assert "calBody.classList.contains(calendarFullClass)" in calendar_src
    assert "#cal-body.cal-events-full > :is(.cal-grid, .cal-wk-wrap)" in style_src
    assert "#cal-body.cal-calendar-full > .cal-day-detail" in style_src


def test_day_selection_adjusts_drawer_for_event_count():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()

    assert "function _adjustDayDrawerForSelection(body, dateStr)" in calendar_src
    assert "if (!events.length)" in calendar_src
    assert "body.classList.add('cal-calendar-full');" in calendar_src
    assert "localStorage.setItem('odysseus.cal.splitSnap', 'calendar');" in calendar_src
    assert "body.classList.contains('cal-calendar-full')" in calendar_src
    assert "detail.getBoundingClientRect().height <= 48" in calendar_src
    assert "const eventRows = Math.min(events.length, 3);" in calendar_src
    assert "localStorage.removeItem('odysseus.cal.splitSnap');" in calendar_src
    day_click_idx = calendar_src.index("body.querySelectorAll('.cal-day[data-date]')")
    adjust_idx = calendar_src.index("_adjustDayDrawerForSelection(body, d);", day_click_idx)
    second_click_idx = calendar_src.index("if (_selectedDay === d && !drawerAdjusted)", adjust_idx)
    render_idx = calendar_src.index("_render();", adjust_idx)
    assert day_click_idx < adjust_idx < second_click_idx < render_idx


def test_calendar_week_drag_preserves_multiday_event_duration():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()

    assert "function _eventDurationMinutes(ev)" in calendar_src
    drag_idx = calendar_src.index("body.querySelectorAll('.cal-wk-block').forEach")
    duration_idx = calendar_src.index("const durationMin = _eventDurationMinutes(ev);", drag_idx)
    update_idx = calendar_src.index("const newDtend = _addMinutesToLocalIso(newDtstart, durationMin);", duration_idx)

    assert duration_idx < update_idx
    assert "let durationMin = endMin0 - startMin0" not in calendar_src[drag_idx:update_idx]


def test_calendar_open_restores_minimized_before_open_guard():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    open_idx = calendar_src.index("function openCalendar(options = {})")
    restore_idx = calendar_src.index("Modals.isRegistered('calendar-modal') && Modals.isMinimized('calendar-modal')", open_idx)
    open_guard_idx = calendar_src.index("if (_open)", open_idx)

    assert restore_idx < open_guard_idx
    assert "function _restoreCalendarFromMinimized()" in calendar_src
    assert "restoreFn: () => { _restoreCalendarFromMinimized(); }" in calendar_src


def test_calendar_targeted_open_preserves_requested_focus():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()

    assert "const preserveFocus = !!(options && options.preserveFocus)" in calendar_src
    assert "if (!preserveFocus)" in calendar_src
    assert "openCalendar({ preserveFocus: true });" in calendar_src


def test_calendar_event_form_can_manually_save_tags():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()

    assert 'id="cal-f-type"' in calendar_src
    assert "existing?.event_type" in calendar_src
    assert "event_type: document.getElementById('cal-f-type')?.value || ''" in calendar_src
    assert "const typeColor = tag ? (_TYPE_PALETTE[tag] || _TYPE_PALETTE.other) : ''" in calendar_src
    assert 'style="color:${_e(color)};"' in calendar_src
    assert "_typeSel.style.borderColor = typeColor || 'var(--border)'" in calendar_src


def test_calendar_tool_guidance_preserves_manual_tags_on_unrelated_updates():
    schema_src = (ROOT / "src/tool_schemas.py").read_text()
    index_src = (ROOT / "src/tool_index.py").read_text()

    assert "otherwise omit it so manually tagged events keep their existing tag" in schema_src
    assert "omitting preserves manual tags" in schema_src
    assert "otherwise omit it so manually tagged events keep their existing tag" in index_src


def test_calendar_visual_asset_versions_are_bumped():
    versions = []
    for src in [
        (ROOT / rel).read_text()
        for rel in ("static/app.js", "static/js/chatRenderer.js", "static/js/emailInbox.js")
    ] + [email_library_source()]:
        match = re.search(r"calendar\.js\?v=([A-Za-z0-9_-]+)", src)
        assert match
        versions.append(match.group(1))
    assert len(set(versions)) == 1
    index_src = (ROOT / "static/index.html").read_text()
    app_versions = re.findall(r"/static/app\.js\?v=([A-Za-z0-9_-]+)", index_src)
    assert app_versions
    assert len(set(app_versions)) == 1


def test_calendar_settings_are_close_only_and_color_the_name_field():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    app_src = (ROOT / "static/app.js").read_text()

    assert "overlay.dataset.noMinimize = 'true'" in calendar_src
    assert "modal.dataset.noMinimize === 'true'" in app_src
    row_idx = calendar_src.index('class="cal-settings-row"')
    name_idx = calendar_src.index('class="cal-s-name"', row_idx)
    color_idx = calendar_src.index('class="cal-s-color"', row_idx)
    assert name_idx < color_idx
    assert "nameInput.style.borderColor = colorInput.value" in calendar_src
    assert "nameInput.style.backgroundColor = colorInput.value" not in calendar_src
    style_src = app_css()
    toggle_idx = style_src.index(".cal-week-start-toggle")
    button_idx = style_src.index(".cal-week-start-btn", toggle_idx)
    assert "padding: 0;" in style_src[toggle_idx:button_idx]
    assert "height: 24px;" in style_src[toggle_idx:button_idx]
    assert "height: 22px !important;" in style_src[button_idx:button_idx + 300]


def test_calendar_settings_actions_sync_error_and_escape_behavior():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert calendar_src.count('class="cal-settings-actions"') >= 4
    assert "justify-content: flex-end;" in style_src[style_src.index(".cal-settings-actions"):]
    assert 'class="cal-settings-sync-status"' in calendar_src
    assert "status.classList.add('is-error')" in calendar_src
    assert ".cal-settings-sync-status.is-error" in style_src
    assert "color: var(--red);" in style_src[style_src.index(".cal-settings-sync-status.is-error"):]
    assert "top: 2px;" in style_src[style_src.index(".cal-settings-sync-status"):]
    assert "e.stopImmediatePropagation();" in calendar_src
    assert "document.addEventListener('keydown', onSettingsKeydown, true);" in calendar_src
    assert '<span style="position:relative;top:0;">Sync now</span>' in calendar_src
    assert calendar_src.index("Upload a .ics file") < calendar_src.index('id="cal-import-file"')
    assert calendar_src.index("Download a calendar as .ics") < calendar_src.index('class="memory-toolbar-btn cal-s-export-chip"')
    assert calendar_src.index("Pulls events from your CalDAV server") < calendar_src.index('id="cal-settings-sync-now"')


def test_calendar_hides_navigation_in_agenda_and_event_forms():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert "_modal?.classList.toggle('cal-navigation-hidden', _view === 'agenda')" in calendar_src
    assert "_modal?.classList.add('cal-navigation-hidden')" in calendar_src
    assert "${_view === 'agenda' ? '' : '<button class=\"cal-nav cal-toolbar-arrow\" id=\"cal-prev\"" in calendar_src
    assert "${_view === 'agenda' ? '' : '<button class=\"cal-nav cal-toolbar-arrow\" id=\"cal-next\"" in calendar_src
    assert "_view !== 'agenda' && !document.querySelector('.cal-form')" in calendar_src
    assert ".cal-navigation-hidden .cal-side-nav" in style_src


def test_calendar_event_form_escape_cancels_before_modal_close():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    ui_src = (ROOT / "static/js/ui.js").read_text()

    form_guard = ui_src.index("const calendarEventForm = calendarModal?.querySelector('.cal-form');")
    hovered_close = ui_src.index("if (_closeHoveredWindow())", form_guard)
    assert form_guard < hovered_close
    assert "calendarEventForm.querySelector('#cal-f-cancel, #cal-form-mobile-cancel')" in ui_src
    assert "e.stopImmediatePropagation();" in calendar_src[calendar_src.index("if (document.querySelector('.cal-form'))"):]


def test_calendar_from_and_to_month_day_use_theme_highlight():
    style_src = app_css()

    month_idx = style_src.index('input[type="date"]::-webkit-datetime-edit-month-field')
    day_idx = style_src.index('input[type="date"]::-webkit-datetime-edit-day-field', month_idx)
    year_idx = style_src.index('input[type="date"]::-webkit-datetime-edit-year-field', day_idx)
    assert "color: var(--accent, var(--red));" in style_src[month_idx:year_idx]
    assert "color: var(--fg);" in style_src[year_idx:year_idx + 180]


def test_calendar_location_hint_uses_accent_map_pin():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert 'class="cal-loc-hint"' in calendar_src
    assert '<span>Location</span>' in calendar_src
    assert '.cal-loc-input-wrap > input:placeholder-shown ~ .cal-loc-hint' in style_src
    pin_idx = style_src.index('.cal-loc-hint svg')
    assert 'color: var(--accent, var(--red));' in style_src[pin_idx:pin_idx + 100]


def test_calendar_tag_order_prioritizes_personal_work_travel_admin():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()

    assert "const typeOrder = ['!', 'personal', 'work', 'travel', 'admin'" in calendar_src
    assert "const types = ['', 'personal', 'work', 'travel', 'admin'" in calendar_src


def test_long_week_events_keep_their_label_visible_while_scrolling():
    calendar_src = (ROOT / "static/js/calendar.js").read_text()
    style_src = app_css()

    assert 'class="cal-wk-block-label"' in calendar_src
    label_idx = style_src.index(".cal-wk-block-label")
    assert "position: sticky;" in style_src[label_idx:label_idx + 220]
    assert "top: 37px;" in style_src[label_idx:label_idx + 220]
    has_allday_idx = style_src.index(".cal-wk-has-allday .cal-wk-block-label")
    assert "top: 61px;" in style_src[has_allday_idx:has_allday_idx + 120]
    block_idx = style_src.index(".cal-wk-block {")
    assert "overflow: clip;" in style_src[block_idx:label_idx]
    head_idx = style_src.index(".cal-wk-col-head {")
    # .cal-wk-allday and its scrollbar rule may now sit in different
    # stylesheets, so anchoring on the scrollbar rule's offset no longer finds
    # the block. Take whichever .cal-wk-allday block carries the z-index.
    allday_idx = next(
        i for i in _occurrences(style_src, ".cal-wk-allday {")
        if "z-index: 39;" in style_src[i:i + 500]
    )
    now_idx = style_src.index(".cal-wk-now {")
    assert "z-index: 40;" in style_src[head_idx:head_idx + 500]
    assert "z-index: 39;" in style_src[allday_idx:allday_idx + 500]
    assert "z-index: 7;" in style_src[now_idx:now_idx + 500]
    spacer_idx = style_src.index(".cal-wk-rail-spacer {")
    allday_spacer_idx = style_src.index(".cal-wk-rail-allday-spacer {")
    assert "height: 32px;" in style_src[spacer_idx:allday_spacer_idx]
    assert "height: 24px;" in style_src[allday_spacer_idx:allday_spacer_idx + 300]
    assert "weekHasAllDayEvents" in calendar_src
    assert 'class="cal-wk-rail-allday-spacer"' in calendar_src
    assert "weekHasAllDayEvents ? ' cal-wk-has-allday' : ''" in calendar_src
    zoom_idx = style_src.index(".cal-wk-zoom {", allday_spacer_idx)
    assert "top: -3px;" in style_src[zoom_idx:zoom_idx + 180]
    assert "border-radius: 50%;" in style_src[zoom_idx:zoom_idx + 320]
    settings_icon_idx = style_src.index("#cal-settings > svg {")
    assert "top: 3px !important;" in style_src[settings_icon_idx:settings_icon_idx + 100]
