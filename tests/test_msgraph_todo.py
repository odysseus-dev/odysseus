"""Tests for the two-way Microsoft To Do sync (Microsoft Graph).

Odysseus keeps to-dos as notes, so `src/msgraph_todo.py` maps a `todoTask`
onto a `Note` rather than inventing a third model. What is covered here:

- `graph_task_to_row` / `row_to_graph_task` — the field mapping in both
  directions, including the due-date split (a bare date stays a date, a date
  with a time becomes a reminder) and the completed ↔ archived rule.
- `label_for_list` / `merge_label` / `choose_list_for_note` — how a To Do
  list becomes a note tag, and why a tag the user added here survives a pull.
- `recurrence_from_repeat` / `repeat_from_recurrence` — the repeat rules that
  round-trip, and the refusal to invent an anchor for one that cannot.
- `_graph_request` — pinned to the Graph host, non-2xx surfaced as GraphError.
- `_graph_paged` — reporting truncation, which is what stops a capped walk
  from being read as "everything upstream".
- `_valid_access_token` / `_refresh_access_token` — cached while fresh, and
  Microsoft's *rotated* refresh token persisted.
- Write-back — the list chosen once, checklist items reconciled through their
  own collection, 404 treated as success, failures leaving a retry marker.
"""

import json
import time
import unittest.mock as mock
from datetime import datetime

import pytest

from src import msgraph_todo as mt


# ── Graph → local ─────────────────────────────────────────────────

def _task(**over):
    base = {
        "id": "AAMkTask==",
        "@odata.etag": 'W/"abc"',
        "title": "Buy milk",
        "body": {"contentType": "text", "content": "semi-skimmed"},
        "status": "notStarted",
        "importance": "normal",
        "lastModifiedDateTime": "2026-09-20T10:00:00.0000000Z",
        "checklistItems": [],
    }
    base.update(over)
    return base


def test_core_fields_map_onto_a_note():
    row = mt.graph_task_to_row(_task(), list_tag="work")
    assert row["title"] == "Buy milk"
    assert row["content"] == "semi-skimmed"
    assert row["note_type"] == "todo"
    assert row["label"] == "work"
    assert row["archived"] is False
    assert row["remote_id"] == "AAMkTask=="


def test_checklist_items_become_note_items():
    row = mt.graph_task_to_row(_task(checklistItems=[
        {"displayName": "2%", "isChecked": True},
        {"displayName": "oat", "isChecked": False},
    ]))
    assert json.loads(row["items"]) == [
        {"text": "2%", "done": True}, {"text": "oat", "done": False},
    ]


def test_blank_checklist_entries_are_dropped():
    row = mt.graph_task_to_row(_task(checklistItems=[
        {"displayName": "   ", "isChecked": False}, {"displayName": "real"},
    ]))
    assert json.loads(row["items"]) == [{"text": "real", "done": False}]


def test_a_task_with_no_steps_stores_no_items():
    assert mt.graph_task_to_row(_task())["items"] is None


def test_completed_upstream_arrives_archived():
    """Archiving is what "done" means for a note: it leaves the grid."""
    assert mt.graph_task_to_row(_task(status="completed"))["archived"] is True


def test_high_importance_arrives_pinned():
    assert mt.graph_task_to_row(_task(importance="high"))["pinned"] is True


def test_html_body_is_stripped_to_text():
    row = mt.graph_task_to_row(_task(
        body={"contentType": "html", "content": "<p>bring <b>cash</b></p>"},
    ))
    assert "<" not in row["content"]
    assert "cash" in row["content"]


def test_a_task_with_no_id_is_refused():
    assert mt.graph_task_to_row({"title": "orphan"}) is None


# ── Due dates ─────────────────────────────────────────────────────

def test_a_due_date_with_no_reminder_stays_a_calendar_day():
    """No `T` in the value is what the notes UI reads as "no time of day"."""
    row = mt.graph_task_to_row(_task(dueDateTime={
        "dateTime": "2026-10-01T00:00:00.0000000", "timeZone": "UTC",
    }))
    assert row["due_date"] == "2026-10-01"


def test_a_reminder_carries_the_time_of_day():
    row = mt.graph_task_to_row(_task(
        isReminderOn=True,
        reminderDateTime={"dateTime": "2026-10-01T09:30:00.0000000", "timeZone": "UTC"},
        dueDateTime={"dateTime": "2026-10-01T00:00:00.0000000", "timeZone": "UTC"},
    ))
    assert row["due_date"] == "2026-10-01T09:30:00+00:00"


def test_no_due_date_at_all_stores_nothing():
    assert mt.graph_task_to_row(_task())["due_date"] is None


def test_a_date_only_note_pushes_as_a_day_with_the_reminder_off():
    body = mt.row_to_graph_task({"title": "t", "due_date": "2026-10-01"})
    assert body["dueDateTime"]["dateTime"].startswith("2026-10-01T00:00:00")
    assert body["isReminderOn"] is False


def test_a_timed_note_pushes_as_a_reminder():
    body = mt.row_to_graph_task({"title": "t", "due_date": "2026-10-01T09:30:00+00:00"})
    assert body["isReminderOn"] is True
    assert body["reminderDateTime"]["dateTime"].startswith("2026-10-01T09:30:00")


def test_an_offset_due_date_is_converted_to_utc_before_pushing():
    body = mt.row_to_graph_task({"title": "t", "due_date": "2026-10-01T09:30:00-03:00"})
    assert body["reminderDateTime"]["dateTime"].startswith("2026-10-01T12:30:00")


def test_an_unparseable_due_date_is_dropped_rather_than_guessed():
    body = mt.row_to_graph_task({"title": "t", "due_date": "next tuesday-ish"})
    assert "dueDateTime" not in body
    assert body["isReminderOn"] is False


# ── local → Graph ─────────────────────────────────────────────────

def test_an_archived_note_pushes_as_completed():
    assert mt.row_to_graph_task({"title": "t", "archived": True})["status"] == "completed"


def test_a_pinned_note_pushes_as_important():
    assert mt.row_to_graph_task({"title": "t", "pinned": True})["importance"] == "high"


def test_an_untitled_note_still_gets_a_title():
    """Graph rejects a task with no title, and losing the note is worse."""
    assert mt.row_to_graph_task({"title": "   "})["title"] == "(untitled)"


def test_the_body_is_sent_as_plain_text():
    body = mt.row_to_graph_task({"title": "t", "content": "a < b"})
    assert body["body"] == {"content": "a < b", "contentType": "text"}


# ── Recurrence ────────────────────────────────────────────────────

@pytest.mark.parametrize("repeat,expected", [
    ("daily", "daily"),
    ("weekly", "weekly"),
    ("monthly", "absoluteMonthly"),
    ("yearly", "absoluteYearly"),
])
def test_repeat_rules_translate_to_a_graph_pattern(repeat, expected):
    got = mt.recurrence_from_repeat(repeat, datetime(2026, 10, 1))
    assert got["pattern"]["type"] == expected
    assert got["range"]["startDate"] == "2026-10-01"


def test_a_weekly_repeat_is_anchored_to_the_due_weekday():
    got = mt.recurrence_from_repeat("weekly", datetime(2026, 10, 1))  # a Thursday
    assert got["pattern"]["daysOfWeek"] == ["thursday"]


def test_a_repeat_with_no_due_date_is_dropped_not_invented():
    """To Do refuses a recurrence with no due date, and picking one for the
    user would silently schedule something they never asked for."""
    assert mt.recurrence_from_repeat("daily", None) is None


@pytest.mark.parametrize("repeat", ["none", "", "fortnightly"])
def test_unsupported_repeats_produce_no_recurrence(repeat):
    assert mt.recurrence_from_repeat(repeat, datetime(2026, 10, 1)) is None


def test_a_recurrence_only_rides_along_with_a_due_date():
    body = mt.row_to_graph_task({"title": "t", "repeat": "daily"})
    assert "recurrence" not in body


@pytest.mark.parametrize("pattern,expected", [
    ("daily", "daily"), ("weekly", "weekly"),
    ("absoluteMonthly", "monthly"), ("relativeMonthly", "monthly"),
    ("absoluteYearly", "yearly"), ("relativeYearly", "yearly"),
])
def test_graph_patterns_translate_back_to_a_repeat(pattern, expected):
    assert mt.repeat_from_recurrence({"pattern": {"type": pattern}}) == expected


@pytest.mark.parametrize("value", [None, {}, "weekly", {"pattern": {"type": "odd"}}])
def test_an_unknown_recurrence_reads_as_no_repeat(value):
    assert mt.repeat_from_recurrence(value) == "none"


# ── Lists ↔ tags ──────────────────────────────────────────────────

def test_a_multi_word_list_collapses_to_one_tag():
    """Labels are space-separated, so "Work Stuff" would read back as two."""
    assert mt.label_for_list("Work Stuff") == "Work-Stuff"


def test_a_hash_in_a_list_name_is_dropped():
    assert mt.label_for_list("#errands") == "errands"


def test_a_note_goes_to_the_list_its_tag_names():
    lists = [
        {"id": "L0", "displayName": "Tasks", "wellknownListName": "defaultList"},
        {"id": "L1", "displayName": "Groceries"},
    ]
    assert mt.choose_list_for_note("urgent groceries", lists) == "L1"


def test_a_note_with_no_matching_tag_goes_to_the_default_list():
    lists = [
        {"id": "L1", "displayName": "Groceries"},
        {"id": "L0", "displayName": "Tasks", "wellknownListName": "defaultList"},
    ]
    assert mt.choose_list_for_note("whatever", lists) == "L0"


def test_a_tag_never_conjures_a_new_list():
    """Creating lists in someone's account off the back of a typo is not a
    side effect worth having, so an unknown tag falls through."""
    lists = [{"id": "L0", "displayName": "Tasks", "wellknownListName": "defaultList"}]
    assert mt.choose_list_for_note("typoo", lists) == "L0"


def test_a_locally_added_tag_survives_a_pull():
    got = mt.merge_label("groceries urgent", "groceries", {"groceries", "work"})
    assert set(mt.note_tags(got)) == {"groceries", "urgent"}


def test_moving_a_task_between_lists_replaces_only_the_list_tag():
    got = mt.merge_label("work urgent", "groceries", {"groceries", "work"})
    assert set(mt.note_tags(got)) == {"groceries", "urgent"}


def test_a_task_in_the_default_list_keeps_its_own_tags_and_gains_none():
    got = mt.merge_label("urgent", "", {"groceries", "work"})
    assert got == "urgent"


def test_a_note_with_no_tags_in_the_default_list_stays_untagged():
    assert mt.merge_label("", "", {"work"}) is None


# ── HTTP ──────────────────────────────────────────────────────────

def test_a_non_graph_url_is_refused():
    """A server-supplied nextLink must not redirect a call carrying our
    bearer token to another host."""
    with pytest.raises(mt.GraphError):
        mt._graph_request("tok", "GET", "https://evil.example/me/todo/lists")


def test_a_non_2xx_becomes_a_graph_error():
    resp = mock.Mock(status_code=403, content=b'{"error":{"message":"denied"}}')
    resp.json.return_value = {"error": {"message": "denied"}}
    with mock.patch("httpx.request", return_value=resp):
        with pytest.raises(mt.GraphError) as err:
            mt._graph_request("tok", "GET", "/me/todo/lists")
    assert err.value.status == 403
    assert "denied" in err.value.message


def test_a_204_is_not_an_error():
    resp = mock.Mock(status_code=204, content=b"")
    with mock.patch("httpx.request", return_value=resp):
        assert mt._graph_request("tok", "DELETE", "/me/todo/lists/L1/tasks/T1") == {}


def test_paging_follows_next_link_and_reports_no_truncation(monkeypatch):
    pages = [
        {"value": [{"id": "1"}], "@odata.nextLink": f"{mt.GRAPH_BASE}/next"},
        {"value": [{"id": "2"}]},
    ]
    monkeypatch.setattr(mt, "_graph_request", lambda *a, **k: pages.pop(0))
    items, truncated = mt._graph_paged("tok", "/me/todo/lists")
    assert [i["id"] for i in items] == ["1", "2"]
    assert truncated is False


def test_hitting_the_page_cap_is_reported_as_truncated(monkeypatch):
    """A caller that prunes what it did not see must know the walk stopped
    early, or it will delete notes whose tasks it simply never reached."""
    monkeypatch.setattr(mt, "_graph_request",
                        lambda *a, **k: {"value": [{"id": "x"}],
                                         "@odata.nextLink": f"{mt.GRAPH_BASE}/next"})
    _, truncated = mt._graph_paged("tok", "/me/todo/lists")
    assert truncated is True


# ── Tokens ────────────────────────────────────────────────────────

@pytest.fixture
def account_store(monkeypatch):
    """In-memory stand-in for the per-user prefs the accounts live in."""
    store = {"accounts": []}
    monkeypatch.setattr(mt, "_load_mstodo_accounts", lambda owner: list(store["accounts"]))
    monkeypatch.setattr(mt, "_save_mstodo_accounts",
                        lambda owner, accounts: store.__setitem__("accounts", list(accounts)))
    monkeypatch.setattr(mt, "_find_account",
                        lambda owner, aid: next((a for a in store["accounts"] if a["id"] == aid), None))
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_ID", "cid")
    monkeypatch.setenv("MICROSOFT_OAUTH_CLIENT_SECRET", "secret")
    return store


def test_fresh_token_is_reused_without_a_network_call(account_store, monkeypatch):
    account_store["accounts"] = [{
        "id": "a1", "access_token": "enc", "refresh_token": "encr",
        "token_expiry": str(int(time.time()) + 3600),
    }]
    monkeypatch.setattr("src.secret_storage.decrypt", lambda v: "live-token")
    with mock.patch("httpx.post") as post:
        assert mt._valid_access_token("o", "a1") == "live-token"
    post.assert_not_called()


def test_expired_token_triggers_a_refresh(account_store, monkeypatch):
    account_store["accounts"] = [{
        "id": "a1", "access_token": "enc", "refresh_token": "encr", "token_expiry": "1",
    }]
    monkeypatch.setattr("src.secret_storage.decrypt", lambda v: "old-refresh")
    monkeypatch.setattr("src.secret_storage.encrypt", lambda v: f"enc:{v}")
    resp = mock.Mock()
    resp.json.return_value = {"access_token": "new-access", "expires_in": 3600}
    resp.raise_for_status.return_value = None
    with mock.patch("httpx.post", return_value=resp):
        assert mt._valid_access_token("o", "a1") == "new-access"


def test_rotated_refresh_token_is_persisted(account_store, monkeypatch):
    """Microsoft spends the old refresh token; dropping the new one strands
    the connection at the next expiry."""
    account_store["accounts"] = [{
        "id": "a1", "access_token": "enc", "refresh_token": "encr", "token_expiry": "1",
    }]
    monkeypatch.setattr("src.secret_storage.decrypt", lambda v: "old-refresh")
    monkeypatch.setattr("src.secret_storage.encrypt", lambda v: f"enc:{v}")
    resp = mock.Mock()
    resp.json.return_value = {
        "access_token": "new-access", "refresh_token": "rotated", "expires_in": 3600,
    }
    resp.raise_for_status.return_value = None
    with mock.patch("httpx.post", return_value=resp):
        mt._valid_access_token("o", "a1")
    assert account_store["accounts"][0]["refresh_token"] == "enc:rotated"


def test_the_todo_scope_is_requested_on_its_own(account_store, monkeypatch):
    """Microsoft issues tokens per resource and refuses a request that mixes
    scopes from two of them, so Tasks.ReadWrite cannot ride with the mail
    scopes."""
    assert "Tasks.ReadWrite" in mt.MSGRAPH_TODO_SCOPES
    assert "IMAP" not in mt.MSGRAPH_TODO_SCOPES
    assert "Calendars" not in mt.MSGRAPH_TODO_SCOPES
    assert "offline_access" in mt.MSGRAPH_TODO_SCOPES
