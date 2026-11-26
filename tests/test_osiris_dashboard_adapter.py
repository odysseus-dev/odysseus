"""Contracts for the bounded OSIRIS dashboard layer tool."""
import importlib.util
import inspect
import json
from pathlib import Path

import pytest


SERVER_PATH = (
    Path(__file__).resolve().parents[1] / "data" / "osint-mcp" / "server.py"
)
_spec = importlib.util.spec_from_file_location("osiris_dashboard_server", SERVER_PATH)
server = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(server)


EXPECTED_LAYERS = {
    "flights", "private", "jets", "military", "maritime", "satellites",
    "cctv", "cctv_previews", "live_news", "earthquakes", "fires",
    "global_incidents", "day_night", "cables", "sdk_sea", "sdk_air",
    "sdk_naval", "live_clouds", "wind", "rain", "wind_controls",
    "rain_controls",
}


def test_dashboard_tool_is_in_osint_family():
    from src.tool_index import OSIRIS_TOOL_NAMES

    assert "mcp__osiris01__osiris_dashboard_layer" in OSIRIS_TOOL_NAMES
    assert len(OSIRIS_TOOL_NAMES) == 3


def test_dashboard_allowlist_matches_requested_layers():
    assert set(server.DASHBOARD_LAYERS) | set(server.DERIVED_LAYERS) == EXPECTED_LAYERS


def test_dashboard_uses_fixed_base_and_bounded_caps():
    assert server.BASE_URL == "https://osirisai.live"
    assert server.TIMEOUT_SECONDS == 25
    assert server.MAX_DASHBOARD_BYTES == 8_000_000
    assert server.MAX_RESULT_CHARS == 14_000
    assert server.MAX_DASHBOARD_LIMIT == 200
    assert all(path.startswith("/") for path in server.FEEDS.values())
    assert all(spec["path"].startswith("/") for spec in server.DASHBOARD_LAYERS.values())


def test_dashboard_has_no_arbitrary_url_parameter():
    parameters = inspect.signature(server.osiris_dashboard_layer).parameters
    assert "url" not in parameters
    assert "path" not in parameters
    assert "key" not in parameters


def test_unknown_layer_is_rejected():
    result = server.osiris_dashboard_layer("not-a-layer")
    assert result["ok"] is False
    assert result["error"] == "unknown_layer"
    assert "wind" in result["available"]


def test_cctv_requires_a_filter():
    result = server.osiris_dashboard_layer("cctv")
    assert result == {"ok": False, "error": "cctv_filter_required"}


@pytest.mark.parametrize("kwargs", [
    {"region": "uk", "lat": 51.5},
    {"lat": 200, "lng": 0},
    {"lat": 51.5, "lng": -0.1, "radius": 501},
])
def test_cctv_filters_are_validated(kwargs):
    result = server.osiris_dashboard_layer("cctv", **kwargs)
    assert result["ok"] is False


def test_wind_time_parameters_must_be_paired():
    result = server.osiris_dashboard_layer("wind", run="2026-11-25T18:00:00.000Z")
    assert result == {"ok": False, "error": "run_and_time_required_together"}


def test_dashboard_limit_is_capped():
    result = server.osiris_dashboard_layer("flights", limit=201)
    assert result == {"ok": False, "error": "invalid_limit"}


def test_dashboard_projects_rows_before_serialization(monkeypatch):
    captured = {}

    def fake_fetch(path, params, max_bytes=None, truncate=True):
        captured.update(path=path, params=params, max_bytes=max_bytes, truncate=truncate)
        return {
            "ok": True,
            "status": 200,
            "url": "https://osirisai.live/api/flights",
            "characters": 100,
            "truncated": False,
            "payload": {
                "commercial_flights": [
                    {"callsign": "UAL573", "lat": 34.2, "lng": -118.3, "secret": "drop"},
                ],
                "total": 1,
                "timestamp": "2026-11-26T00:00:00Z",
            },
        }

    monkeypatch.setattr(server, "_fetch", fake_fetch)
    result = server.osiris_dashboard_layer("flights", limit=1)
    payload = json.loads(result["result"])

    assert captured["path"] == "/api/flights"
    assert captured["truncate"] is False
    assert captured["max_bytes"] == server.MAX_DASHBOARD_BYTES
    assert payload["commercial_flights"] == [
        {"callsign": "UAL573", "lat": 34.2, "lng": -118.3},
    ]
    assert "secret" not in result["result"]
    assert result["truncated"] is False


def test_cctv_previews_return_only_projected_camera_fields(monkeypatch):
    def fake_fetch(path, params, max_bytes=None, truncate=True):
        assert path == "/api/cctv"
        assert params == {"region": "uk"}
        return {
            "ok": True,
            "status": 200,
            "url": "https://osirisai.live/api/cctv?region=uk",
            "characters": 100,
            "truncated": False,
            "payload": {
                "cameras": [{
                    "id": "cam-1", "name": "A406", "city": "London",
                    "country": "UK", "lat": 51.6, "lng": -0.01,
                    "feed_url": "https://example.invalid/cam.jpg",
                    "stream_url": "https://example.invalid/cam.mp4",
                    "internal": "drop",
                }],
                "total": 1,
            },
        }

    monkeypatch.setattr(server, "_fetch", fake_fetch)
    result = server.osiris_dashboard_layer("cctv_previews", region="uk")
    payload = json.loads(result["result"])

    assert payload["cameras"][0]["feed_url"] == "https://example.invalid/cam.jpg"
    assert payload["cameras"][0]["stream_url"] == "https://example.invalid/cam.mp4"
    assert "internal" not in result["result"]
    assert any("does not open" in note for note in result["notes"])


def test_derived_layers_do_not_fetch(monkeypatch):
    called = []

    def fail_fetch(*args, **kwargs):
        called.append((args, kwargs))
        raise AssertionError("derived layer must not fetch")

    monkeypatch.setattr(server, "_fetch", fail_fetch)
    result = server.osiris_dashboard_layer("day_night")
    assert result["ok"] is True
    assert not called


def test_rain_layer_returns_frame_metadata_only(monkeypatch):
    def fake_fetch(path, params, max_bytes=None, truncate=True):
        assert path == "/api/rain"
        return {
            "ok": True,
            "status": 200,
            "url": "https://osirisai.live/api/rain",
            "characters": 100,
            "truncated": False,
            "payload": {
                "host": "https://tilecache.rainviewer.com",
                "frames": [
                    {"time": 1795643400000, "path": "/v2/radar/abc"},
                    {"time": 1795644000000, "path": "/v2/radar/def"},
                ],
            },
        }

    monkeypatch.setattr(server, "_fetch", fake_fetch)
    result = server.osiris_dashboard_layer("rain")
    payload = json.loads(result["result"])

    assert payload["frame_count"] == 2
    assert payload["latest_time"] == 1795644000000
    assert payload["paths"] == ["/v2/radar/abc", "/v2/radar/def"]
