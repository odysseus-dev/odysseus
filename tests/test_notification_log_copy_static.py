from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def test_notification_copy_supports_insecure_lan_contexts():
    source = (ROOT / "static/js/admin.js").read_text(encoding="utf-8")

    assert "async function copyNotificationText(value)" in source
    assert "navigator.clipboard?.writeText && window.isSecureContext" in source
    assert "document.execCommand('copy')" in source
    assert "await copyNotificationText(note.body)" in source
    assert "event.stopPropagation();" in source
