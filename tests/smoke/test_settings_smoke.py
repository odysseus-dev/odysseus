"""Settings: a preference written through the API survives a new login.

Reading the value back on the same cookie only proves the handler
answered. Reading it back after authenticating again is what proves it
was persisted rather than held in the session, which is the closest an
API-level check gets to the user reloading the page.
"""
from __future__ import annotations

PREFS_PATH = "/api/prefs"

KEY = "odysseus_smoke_preference"
VALUE = "set-by-the-release-smoke-suite"


def test_a_preference_survives_a_new_login(client, fresh_client):
    written = client.put(f"{PREFS_PATH}/{KEY}", json={"value": VALUE})
    assert written.status_code == 200, written.text
    assert written.json().get("value") == VALUE, written.text

    read = client.get(f"{PREFS_PATH}/{KEY}")
    assert read.status_code == 200, read.text
    assert read.json().get("value") == VALUE, read.text

    reloaded = fresh_client.get(f"{PREFS_PATH}/{KEY}")
    assert reloaded.status_code == 200, reloaded.text
    assert reloaded.json().get("value") == VALUE, reloaded.text

    listed = fresh_client.get(PREFS_PATH)
    assert listed.status_code == 200, listed.text
    assert listed.json().get(KEY) == VALUE, listed.text
