"""Behavior of the Settings scope modules, run in Node.

Covers static/js/settings/modelDefaults.js (where model defaults are stored
and how failures are reported), static/js/authStatus.js (the access summary
derived from /api/auth/status) and describeSectionScope() in
static/js/settings/scope.js (which cards are labelled or locked for whom).
"""

import json
import shutil
import subprocess
from pathlib import Path

import pytest


_REPO = Path(__file__).resolve().parent.parent
_RUNNER = _REPO / "tests" / "helpers" / "settings_scope_runner.mjs"


@pytest.fixture(scope="module")
def results():
    if shutil.which("node") is None:
        pytest.skip("node binary not on PATH")
    proc = subprocess.run(
        ["node", str(_RUNNER)],
        cwd=str(_REPO),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=60,
    )
    assert proc.returncode == 0, f"STDERR:\n{proc.stderr}\nSTDOUT:\n{proc.stdout}"
    return json.loads(proc.stdout.strip())


def test_personal_store_reads_and_writes_only_the_callers_preferences(results):
    personal = results["personal"]
    assert personal["personal"] is True
    assert personal["calls"] == ["GET /api/prefs", "PATCH /api/prefs"]
    # Only model-default keys are surfaced, never unrelated preferences.
    assert personal["loaded"] == {"default_endpoint_id": "ep-a", "default_model": "m"}
    # An empty choice removes the personal value instead of storing "".
    assert personal["patch"] == {"values": {
        "default_endpoint_id": None,
        "default_model": None,
        "utility_model_fallbacks": None,
    }}


def test_server_store_writes_settings_then_clears_own_overrides(results):
    server = results["server"]
    assert server["calls"] == ["GET /api/auth/settings", "POST /api/auth/settings", "PATCH /api/prefs"]
    assert server["loaded"] == {"default_endpoint_id": "ep-g", "default_model": "g"}
    assert server["posted"] == {"default_endpoint_id": "ep-b", "default_model": "b"}
    assert server["cleared"] == {"values": {"default_endpoint_id": None, "default_model": None}}


def test_auth_disabled_operator_never_touches_preferences(results):
    assert results["operator"] == ["POST /api/auth/settings"]


def test_clearing_failure_reports_that_the_server_value_was_saved(results):
    failure = results["clearFailure"]
    assert failure["posted"] is True
    assert failure["serverSaved"] is True
    assert failure["message"].startswith("Saved for the server")


def test_failures_carry_status_and_refused_server_saves_stop(results):
    failures = results["failures"]
    assert failures["personalStatus"] == 500
    assert failures["loadStatus"] == 500
    assert failures["serverStatus"] == 403
    assert failures["clearedAfterRefusal"] is False
    assert "permission" in failures["serverMessage"]
    assert "currently stored" in failures["genericMessage"]


def test_store_refuses_keys_outside_the_model_defaults(results):
    assert results["unknownKey"] == {"refused": True, "requests": 0}
    assert not any(key.startswith("foreground_") for key in results["keys"])


def test_saves_run_in_order_and_a_failure_does_not_block_the_next(results):
    assert results["queue"] == {"outcomes": ["failed", "ok"], "order": ["first", "second"]}


def test_access_summary_matrix(results):
    access = results["access"]
    assert access["admin"] == {"authEnabled": True, "canManage": True, "personalModelDefaults": False}
    assert access["member"] == {"authEnabled": True, "canManage": False, "personalModelDefaults": True}
    assert access["operator"] == {"authEnabled": False, "canManage": True, "personalModelDefaults": False}
    # Missing fields read as the most restrictive presentation.
    assert access["legacyAdmin"]["canManage"] is True
    assert access["empty"]["canManage"] is False
    assert access["invalid"]["canManage"] is False


def test_scope_presentation_matrix(results):
    scope = results["scope"]
    assert scope["admin"]["modelDefault"]["badge"] == "Server default"
    assert scope["admin"]["server"] == {
        "badge": "All users",
        "title": "Applies to everyone on this server.",
        "locked": False,
    }
    assert scope["member"]["modelDefault"]["badge"] == "Just you"
    assert scope["member"]["modelDefault"]["locked"] is False
    assert scope["member"]["server"]["locked"] is True
    assert scope["member"]["server"]["note"]
    # No accounts, or unknown access: no labels and nothing locked.
    assert scope["operator"] == {"modelDefault": None, "server": None}
    assert scope["unknownAccess"] == {"modelDefault": None, "server": None}
    assert scope["noScope"] is None
