"""Regression guard for #4766: the nightly skill audit defaults to opt-in."""
import pathlib

import src.settings as settings


def test_skill_audit_nightly_not_default_on_via_settings():
    """The nightly skill audit must not be silently enabled through the settings
    defaults — it is unattended LLM work that can cost tokens on a paid endpoint.
    Absent an explicit user setting it must read falsy.
    """
    assert "skill_audit_nightly" not in getattr(settings, "DEFAULT_SETTINGS", {})


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
