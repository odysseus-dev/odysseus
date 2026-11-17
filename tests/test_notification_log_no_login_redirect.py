from pathlib import Path

APP_JS = (Path(__file__).resolve().parents[1] / "static" / "app.js").read_text(encoding="utf-8")


def test_best_effort_toast_log_never_triggers_login_redirect():
    # With auth disabled POST /api/tasks/notification-logs answers 401 for every
    # toast; the global 401 handler must not turn that into a page navigation.
    marker = "res.status === 401"
    line = next(l for l in APP_JS.splitlines() if marker in l)
    assert "/api/auth/" in line
    assert "/api/tasks/notification-logs" in line
