"""Regression guard for issue #1894 — NotImplementedError on Windows + Python 3.14.

asyncio.create_subprocess_exec raises NotImplementedError when the running
event loop is a SelectorEventLoop (the default on Windows, and the loop used
by uvicorn --reload worker processes). The previous code only caught OSError
and ValueError, so the exception propagated and crashed the _start_npx_servers
background task on every Windows startup.

Fix: catch NotImplementedError in _is_npx_package_cached and fall back to a
synchronous subprocess.run with the same timeout bound.
"""
import re
from pathlib import Path

SRC = Path(__file__).resolve().parent.parent / "src/builtin_mcp.py"


def _cached_fn_body() -> str:
    text = SRC.read_text(encoding="utf-8")
    start = text.index("async def _is_npx_package_cached(")
    rest = text[start:]
    # Find the next top-level function/class definition
    m = re.search(r"\n(async def |def |class )", rest[1:])
    return rest[: m.start() + 1] if m else rest


def test_notimplemented_is_caught():
    body = _cached_fn_body()
    assert "NotImplementedError" in body, (
        "_is_npx_package_cached must catch NotImplementedError "
        "(raised by SelectorEventLoop on Windows when creating async subprocesses)"
    )


def test_sync_fallback_uses_subprocess_run():
    body = _cached_fn_body()
    assert "subprocess.run" in body, (
        "fallback path must use subprocess.run for synchronous execution"
    )


def test_sync_fallback_passes_timeout():
    body = _cached_fn_body()
    assert re.search(r"subprocess\.run\(.*timeout=timeout_s", body, re.DOTALL), (
        "subprocess.run fallback must pass timeout=timeout_s to stay bounded"
    )


def test_oserror_still_caught():
    body = _cached_fn_body()
    assert "OSError" in body, (
        "OSError must still be caught after adding NotImplementedError handler"
    )
