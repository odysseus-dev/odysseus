from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_calendar_location_pin_uses_google_maps_sheet():
    calendar_js = (ROOT / "static" / "js" / "calendar.js").read_text(encoding="utf-8")

    assert "maps.apple.com" not in calendar_js
    assert "openstreetmap.org" not in calendar_js
    assert "Preview location in Google Maps" in calendar_js
    assert "https://www.google.com/maps/search/?" in calendar_js
    assert "https://www.google.com/maps/dir/?" in calendar_js
    assert "https://www.google.com/maps?" in calendar_js
    assert "output: 'embed'" in calendar_js
    assert "_openLocationMapSheet" in calendar_js
    assert "CAL_MAP_TRAVEL_MODES" in calendar_js
    assert "CAL_MAP_NEARBY" in calendar_js
    assert "navigator.share" in calendar_js


def test_android_shell_routes_google_maps_main_frame_links_externally():
    main_activity = (
        ROOT
        / "android"
        / "app"
        / "src"
        / "main"
        / "java"
        / "com"
        / "odysseus"
        / "simplesignal"
        / "MainActivity.java"
    ).read_text(encoding="utf-8")

    assert "isGoogleMapsUrl" in main_activity
    assert "request.isForMainFrame() && isGoogleMapsUrl(uri)" in main_activity
    assert "openExternalUri(uri)" in main_activity
