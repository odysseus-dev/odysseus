"""Regression guard for #4766 / PR #4828: the nightly skill audit is opt-in.

It must default to off (no unattended token spend) AND be a real, registered
setting so the normal settings path can turn it back on. The second half is the
fix for the PR #4828 review: the admin settings writers (manage_settings, the
admin settings route) only persist keys present in DEFAULT_SETTINGS, so an
unregistered key would be default-off with no supported way to re-enable it.
"""
import pathlib

import src.settings as settings


def test_skill_audit_nightly_registered_off_by_default():
    """Registered in DEFAULT_SETTINGS with a False default: opt-in (won't run
    unattended) and still settable through the normal settings machinery."""
    assert settings.DEFAULT_SETTINGS.get("skill_audit_nightly") is False


def test_skill_audit_nightly_can_be_enabled_through_settings_writer(tmp_path, monkeypatch):
    """A user must be able to turn the audit back on through the normal settings
    path. manage_settings and the admin settings route only persist keys present
    in DEFAULT_SETTINGS, so registering the key is what makes the opt-in real.
    Round-trips save/get_setting against an isolated settings file.
    """
    monkeypatch.setattr(settings, "SETTINGS_FILE", str(tmp_path / "settings.json"))
    settings._invalidate_caches()
    try:
        assert settings.get_setting("skill_audit_nightly") is False  # default off
        settings.save_settings({**settings.load_settings(), "skill_audit_nightly": True})
        assert settings.get_setting("skill_audit_nightly") is True   # opt-in works
    finally:
        settings._invalidate_caches()


def test_nightly_skill_audit_gate_defaults_off():
    """Pin #4766: the gate in app.py defaults the setting to off (opt-in).

    The gate lives in app.py, which the suite intentionally cannot import
    (tests/conftest.py stubs src.database so importing app fails), and the loop
    only fires at ~02:00 — so this can't be driven at runtime. Per
    TESTING_STANDARD's narrow exception, assert the invariant on the source.
    """
    app_src = (pathlib.Path(__file__).resolve().parents[1] / "app.py").read_text(encoding="utf-8")
    assert 'get_setting("skill_audit_nightly", False)' in app_src
    assert 'get_setting("skill_audit_nightly", True)' not in app_src
