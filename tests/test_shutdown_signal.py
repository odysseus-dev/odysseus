"""Graceful-shutdown signalling tests (HARNESS-owned).

SSE / streaming responses never complete on their own, so uvicorn's graceful
shutdown would wait for an open browser stream up to the whole
``timeout_graceful_shutdown`` window. src.shutdown is a process-wide flag that
the SIGTERM/SIGINT handler flips and every streaming generator checks, so a
restart ends open streams promptly while ordinary in-flight requests still
finish.
"""
from pathlib import Path

from src import shutdown

ROOT = Path(__file__).resolve().parent.parent


def test_request_shutdown_is_sticky_and_resettable():
    shutdown.reset()
    try:
        assert shutdown.is_shutting_down() is False
        shutdown.request_shutdown()
        assert shutdown.is_shutting_down() is True
        # idempotent
        shutdown.request_shutdown()
        assert shutdown.is_shutting_down() is True
    finally:
        shutdown.reset()
    assert shutdown.is_shutting_down() is False


def test_app_installs_its_own_shutdown_handler():
    src = (ROOT / "app.py").read_text(encoding="utf-8")
    # This uvicorn version has no install_signal_handlers kwarg, so app.py wraps
    # server.handle_exit (which capture_signals wires SIGINT/SIGTERM to).
    assert "install_signal_handlers=False" not in src
    assert "server.handle_exit = _handle_shutdown_signal" in src
    assert "_shutdown.request_shutdown()" in src
    assert "server.run()" in src


def test_streaming_generators_check_the_shutdown_flag():
    # editor bridge SSE + chat stream/resume + research + email events + shell.
    for rel in (
        "routes/editor_agent_routes.py",
        "routes/chat_routes.py",
        "routes/research/research_routes.py",
        "routes/email/email_routes.py",
        "routes/shell_routes.py",
    ):
        src = (ROOT / rel).read_text(encoding="utf-8")
        assert "_shutdown.is_shutting_down()" in src, rel
