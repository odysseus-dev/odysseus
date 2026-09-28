"""Regression coverage for the Microsoft To Do wiring in the frontend.

Both modules pull in the DOM and a dozen siblings, so these read the source
rather than booting it — the same idiom as `test_msgraph_calendar_ui.py`.
"""

import re
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def settings():
    return (_REPO / "static" / "js" / "settings.js").read_text(encoding="utf-8")


@pytest.fixture(scope="module")
def notes():
    return (_REPO / "static" / "js" / "notes.js").read_text(encoding="utf-8")


# ── Integration card ──────────────────────────────────────────────

def test_microsoft_to_do_is_an_integration_type(settings):
    assert re.search(r"mstodo:\s*\{\s*label:\s*'Microsoft To Do'", settings)


def test_connected_accounts_are_fetched_with_the_rest(settings):
    assert "fetch('/api/notes/config/microsoft'" in settings


def test_a_card_is_built_per_connected_account(settings):
    assert "for (const acc of (msTodoRes.accounts || []))" in settings
    assert "type: 'mstodo'," in settings


def test_the_picker_offers_it(settings):
    assert "['mstodo', 'Microsoft To Do']" in settings


def test_the_form_is_reachable_from_the_picker(settings):
    assert "else if (type === 'mstodo') showMsTodoForm();" in settings


def test_disconnecting_calls_the_notes_route(settings):
    assert "/api/notes/config/microsoft/${id}" in settings


def test_connecting_starts_the_notes_oauth_flow(settings):
    assert "'/api/notes/oauth/microsoft/authorize'" in settings


def test_the_form_names_the_permission_the_app_registration_needs(settings):
    """Without Tasks.ReadWrite the consent succeeds and every call 403s."""
    assert "Tasks.ReadWrite" in settings


def test_the_connect_button_is_disabled_without_an_app_registration(settings):
    """Otherwise the button bounces the user to a Microsoft error page."""
    assert "MICROSOFT_OAUTH_CLIENT_ID is not set" in settings
    block = settings[settings.index("async function showMsTodoForm"):]
    block = block[:block.index("// ── CalDAV form")]
    assert "if (!configured) return;" in block


def test_the_oauth_result_banner_covers_the_task_flow(settings):
    assert "{ prefix: 'tasks_oauth', subject: 'task sync' }" in settings


# ── Sync button ───────────────────────────────────────────────────

def test_notes_has_a_sync_button(notes):
    assert 'id="notes-todo-sync"' in notes


def test_the_sync_button_is_hidden_until_an_account_is_connected(notes):
    """A control that can only ever say "nothing connected" is noise."""
    block = notes[notes.index('id="notes-todo-sync"'):]
    assert "display:none;" in block[:block.index("</button>")]
    assert "if (!connected) return;" in notes
    assert "todoSyncBtn.style.display = '';" in notes


def test_sync_pushes_before_pulling(notes):
    """A pull alone would strand a task created here while offline."""
    assert "/api/notes/sync?direction=both" in notes


def test_sync_refreshes_the_board_afterwards(notes):
    block = notes[notes.index("async function _syncTodo"):]
    block = block[:block.index("async function _saveNote")]
    assert "await _fetchNotes();" in block
    assert "_renderNotes();" in block


def test_sync_reports_errors_rather_than_claiming_success(notes):
    block = notes[notes.index("async function _syncTodo"):]
    block = block[:block.index("async function _saveNote")]
    assert "if (data.errors.length)" in block
    assert "uiModule.showError" in block


def test_overlapping_syncs_are_ignored(notes):
    block = notes[notes.index("async function _syncTodo"):]
    block = block[:block.index("async function _saveNote")]
    assert "if (_todoSyncRunning) return;" in block


def test_both_direction_counts_are_added_up(notes):
    """`direction=both` nests its counts under push/pull, so a flat read
    would report zero after a sync that did work."""
    block = notes[notes.index("function _flattenTodoSync"):]
    block = block[:block.index("async function _syncTodo")]
    assert "raw.push || raw.pull" in block


def test_opening_notes_syncs_in_the_background(notes):
    """Same shape as the calendar: a task ticked off on a phone is already
    here without waiting for the user to press Sync."""
    assert "_syncTodo(false);" in notes
    assert "_syncTodo(true);" in notes


def test_the_open_time_sync_is_silent(notes):
    """It redraws only when something moved, so opening Notes does not flash
    the board or toast a sync nobody asked for."""
    block = notes[notes.index("async function _syncTodo"):]
    block = block[:block.index("async function _saveNote")]
    assert "if (interactive || data.tasks > 0 || data.deleted > 0)" in block
    assert "if (!interactive) return;" in block
    assert "if (interactive) uiModule.showError" in block
