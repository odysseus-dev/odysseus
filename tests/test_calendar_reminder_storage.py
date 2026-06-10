from pathlib import Path


REPO = Path(__file__).resolve().parent.parent
REMINDERS_JS = REPO / "static" / "js" / "calendar" / "reminders.js"


def test_calendar_reminders_use_safe_storage_json_loader():
    source = REMINDERS_JS.read_text(encoding="utf-8")

    assert "import Storage from '../storage.js';" in source
    assert "Storage.getJSON('cal-notif-fired', [])" in source
    assert "JSON.parse(localStorage.getItem('cal-notif-fired')" not in source
