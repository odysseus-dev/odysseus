"""Tests for desktop_act: consent flow, target resolution, mic lease, degradation."""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _fresh_state():
    from services.operator import desktop
    from services.operator.core import reset_status_cache

    desktop.reset_consents()
    reset_status_cache()
    yield
    desktop.reset_consents()
    reset_status_cache()


@pytest.fixture(autouse=True)
def _worker_online():
    from services.operator import core

    core._status_cache[core.CAP_DESKTOP_ACTION] = (time.monotonic(), {"available": True})
    yield


def _audit_calls():
    return patch("services.operator.desktop.record_audit")


# ── consent gate ──

def test_first_action_requires_consent():
    from services.operator.desktop import desktop_act

    with _audit_calls() as audit:
        result = desktop_act({"action": "click", "x": 10, "y": 10}, session_id="s1")

    assert result["ok"] is False
    assert result["reason"] == "consent_required"
    assert "ask_user" in result["hint"]
    assert audit.call_args.kwargs["result"] == "denied"


def test_user_approved_grants_consent_and_executes():
    from services.operator import desktop

    with _audit_calls() as audit:
        with patch.object(desktop, "_post_json", return_value=(200, {"ok": True, "action": "click", "x": 10, "y": 10})):
            first = desktop.desktop_act(
                {"action": "click", "x": 10, "y": 10, "user_approved": True}, session_id="s1",
            )
            # Consent persists for the session — second call needs no flag.
            second = desktop.desktop_act({"action": "click", "x": 20, "y": 20}, session_id="s1")

    assert first["ok"] is True
    assert second["ok"] is True
    assert audit.call_count == 2
    assert all(c.kwargs.get("result", "ok") == "ok" for c in audit.call_args_list)


def test_consent_is_per_session():
    from services.operator import desktop

    desktop.grant_consent("s1")
    with _audit_calls():
        result = desktop.desktop_act({"action": "click", "x": 1, "y": 1}, session_id="other")
    assert result["reason"] == "consent_required"


# ── pointer actions ──

def test_click_posts_to_worker_pointer():
    from services.operator import desktop

    desktop.grant_consent("s1")
    seen = {}

    def fake_post(url, payload, timeout=None):
        seen["url"] = url
        seen["payload"] = payload
        return 200, {"ok": True, **payload}

    with _audit_calls():
        with patch.object(desktop, "_post_json", fake_post):
            result = desktop.desktop_act(
                {"action": "drag", "x": 5, "y": 6, "to_x": 50, "to_y": 60}, session_id="s1",
            )

    assert result["ok"] is True
    assert seen["url"].endswith("/pointer")
    assert seen["payload"] == {"action": "drag", "x": 5, "y": 6, "to_x": 50, "to_y": 60}


def test_target_text_resolves_via_ocr_geometry():
    from services.operator import desktop

    desktop.grant_consent("s1")
    resolution = {"resolved": True, "x": 245, "y": 180, "match": {"text": "Submit"}}
    with _audit_calls():
        with patch.object(desktop, "resolve_target", return_value=resolution):
            with patch.object(desktop, "_post_json", return_value=(200, {"ok": True})) as post:
                result = desktop.desktop_act({"action": "click", "target_text": "Submit"}, session_id="s1")

    assert result["ok"] is True
    assert post.call_args.args[1]["x"] == 245
    assert "Submit" in result["data"]["target"]


def test_ambiguous_target_refuses_to_click():
    from services.operator import desktop

    desktop.grant_consent("s1")
    resolution = {
        "resolved": False, "reason": "ambiguous_target",
        "candidates": [{"text": "Submit", "center": (1, 2)}, {"text": "Submit", "center": (3, 4)}],
    }
    with _audit_calls():
        with patch.object(desktop, "resolve_target", return_value=resolution):
            with patch.object(desktop, "_post_json") as post:
                result = desktop.desktop_act({"action": "click", "target_text": "Submit"}, session_id="s1")

    assert result["ok"] is False
    assert result["reason"] == "ambiguous_target"
    assert len(result["data"]["candidates"]) == 2
    post.assert_not_called()


def test_worker_offline_degrades():
    from services.operator import desktop

    desktop.grant_consent("s1")
    with _audit_calls():
        with patch.object(desktop, "_post_json", return_value=(0, {"reason": "connection refused"})):
            result = desktop.desktop_act({"action": "click", "x": 1, "y": 1}, session_id="s1")

    assert result["degraded"] is True
    assert result["reason"] == "clicky_offline"


def test_old_worker_reports_unsupported_action():
    from services.operator import desktop

    desktop.grant_consent("s1")
    with _audit_calls():
        with patch.object(desktop, "_post_json", return_value=(404, {"reason": "not found"})):
            result = desktop.desktop_act({"action": "click", "x": 1, "y": 1}, session_id="s1")

    assert result["reason"] == "unsupported_action"
    assert "start-clicky" in result["hint"]


# ── audio actions ──

def test_listen_returns_mic_busy_without_stealing_lease():
    from services.operator import desktop

    desktop.grant_consent("s1")
    released = []

    fake_lease = type("M", (), {
        "claim": staticmethod(lambda holder, mode, ttl_sec: {"ok": False, "holder": "voice"}),
        "release": staticmethod(lambda holder, token=None: released.append(holder)),
    })
    with _audit_calls():
        with patch.dict("sys.modules", {"services.voice.mic_lease": fake_lease}):
            result = desktop.desktop_act({"action": "listen"}, session_id="s1")

    assert result["reason"] == "mic_busy"
    assert "voice" in result["hint"]
    assert released == []  # never released a lease it didn't get


def test_listen_releases_lease_when_unsupported():
    from services.operator import desktop

    desktop.grant_consent("s1")
    released = []

    fake_lease = type("M", (), {
        "claim": staticmethod(lambda holder, mode, ttl_sec: {"ok": True, "token": "t1"}),
        "release": staticmethod(lambda holder, token=None: released.append((holder, token))),
    })
    with _audit_calls():
        with patch.dict("sys.modules", {"services.voice.mic_lease": fake_lease}):
            result = desktop.desktop_act({"action": "listen"}, session_id="s1")

    assert result["reason"] == "unsupported_action"
    assert released == [("operator", "t1")]


def test_speak_posts_text_to_worker_tts():
    from services.operator import desktop

    desktop.grant_consent("s1")
    with _audit_calls():
        with patch.object(desktop, "_post_json", return_value=(200, {})) as post:
            result = desktop.desktop_act({"action": "speak", "text": "hello"}, session_id="s1")

    assert result["ok"] is True
    assert post.call_args.args[0].endswith("/tts")
    assert post.call_args.args[1] == {"text": "hello"}


# ── target resolution parsing ──

def test_resolve_target_unique_geometry_match():
    from services.operator import desktop

    body = {
        "data": [
            {"content": {
                "app_name": "Chrome", "window_name": "App",
                "text_json": [
                    {"text": "Submit order", "left": 100, "top": 200, "width": 90, "height": 30},
                    {"text": "Cancel", "left": 300, "top": 200, "width": 60, "height": 30},
                ],
            }},
        ]
    }

    class _Resp:
        def read(self):
            return json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch.object(desktop.request, "urlopen", lambda req, timeout=None: _Resp()):
        result = desktop.resolve_target("Submit")

    assert result["resolved"] is True
    assert (result["x"], result["y"]) == (145, 215)


def test_resolve_target_without_geometry_reports_unavailable():
    from services.operator import desktop

    body = {"data": [{"content": {"text": "Submit order", "app_name": "Chrome"}}]}

    class _Resp:
        def read(self):
            return json.dumps(body).encode()

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    with patch.object(desktop.request, "urlopen", lambda req, timeout=None: _Resp()):
        result = desktop.resolve_target("Submit")

    assert result["resolved"] is False
    assert result["reason"] == "target_resolution_unavailable"
    assert result["candidates"][0]["text"].startswith("Submit")


# ── worker pointer endpoint ──

def test_worker_pointer_rejects_unknown_action():
    from tools.clicky_worker_api import perform_pointer_action

    status, payload = perform_pointer_action({"action": "teleport"})
    assert status == 400
    assert payload["reason"] == "unsupported_action"


def test_worker_pointer_requires_coordinates():
    from tools.clicky_worker_api import perform_pointer_action

    with patch("tools.clicky_worker_api.os") as fake_os:
        fake_os.name = "nt"
        status, payload = perform_pointer_action({"action": "click"})
    assert status == 400
    assert payload["reason"] == "bad_coordinates"


def test_worker_pointer_non_windows_unsupported():
    from tools.clicky_worker_api import perform_pointer_action

    with patch("tools.clicky_worker_api.os") as fake_os:
        fake_os.name = "posix"
        status, payload = perform_pointer_action({"action": "click", "x": 1, "y": 2})
    assert status == 501
    assert payload["reason"] == "unsupported_platform"


def test_desktop_act_registered():
    from src.agent_tools import TOOL_TAGS
    from src.tool_schemas import FUNCTION_TOOL_SCHEMAS

    names = {t["function"]["name"] for t in FUNCTION_TOOL_SCHEMAS}
    assert "desktop_act" in names
    assert "desktop_act" in TOOL_TAGS
