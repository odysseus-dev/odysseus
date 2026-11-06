"""A subagent may not delegate again."""
from src import subagent_runtime


def test_unbound_loop_cannot_delegate():
    assert subagent_runtime.can_delegate() is False


def test_one_level_only():
    token = subagent_runtime.bind("http://127.0.0.1:1", "demo")
    try:
        assert subagent_runtime.can_delegate() is True
    finally:
        subagent_runtime.reset(token)
    token = subagent_runtime.bind("http://127.0.0.1:1", "demo", depth=1)
    try:
        assert subagent_runtime.can_delegate() is False
    finally:
        subagent_runtime.reset(token)
