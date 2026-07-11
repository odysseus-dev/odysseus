"""Tests for src/agent/loop_detector.py"""
from __future__ import annotations
import json
from src.agent.loop_detector import (
    LoopDetector,
    StableSignature,
    RecoveryLevel,
)


def test_stable_signature_ignores_key_order():
    a = json.dumps({"command": "ls", "path": "/tmp"}, sort_keys=True)
    b = json.dumps({"path": "/tmp", "command": "ls"}, sort_keys=True)
    sig_a = StableSignature.from_tool_call("bash", a)
    sig_b = StableSignature.from_tool_call("bash", b)
    assert sig_a == sig_b


def test_stable_signature_differs_by_args():
    a = StableSignature.from_tool_call("bash", json.dumps({"command": "ls"}))
    b = StableSignature.from_tool_call("bash", json.dumps({"command": "pwd"}))
    assert a != b


def test_stable_signature_differs_by_tool():
    a = StableSignature.from_tool_call("bash", '{"command":"ls"}')
    b = StableSignature.from_tool_call("read_file", '{"command":"ls"}')
    assert a != b


def test_ngram_detection_catches_repetition():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    text = "I need to check the logs. "
    for _ in range(8):
        det.record_round(text=text, tool_calls=[])
    level = det.check_text_loop()
    assert level in (RecoveryLevel.MILD, RecoveryLevel.STRONG)


def test_ngram_no_false_positive_on_varied_text():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    texts = [
        "Let me check the file system.",
        "Now I'll read the configuration.",
        "I found the issue in main.py.",
        "Let me fix the bug.",
    ]
    for t in texts:
        det.record_round(text=t, tool_calls=[])
    level = det.check_text_loop()
    assert level == RecoveryLevel.NONE


def test_stall_detection_repeated_calls_no_text():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    sig = StableSignature.from_tool_call("bash", '{"command":"ls"}')
    for _ in range(5):
        det.record_round(text="", tool_calls=[sig])
    level = det.check_stall()
    assert level in (RecoveryLevel.MILD, RecoveryLevel.STRONG)


def test_runaway_detection_identical_calls():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    sig = StableSignature.from_tool_call("bash", '{"command":"ls -la"}')
    for _ in range(16):
        det.record_round(text="", tool_calls=[sig])
    assert det.is_runaway() is True


def test_runaway_not_triggered_on_distinct_calls():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    for i in range(20):
        sig = StableSignature.from_tool_call("bash", json.dumps({"command": f"cmd_{i}"}))
        det.record_round(text="", tool_calls=[sig])
    assert det.is_runaway() is False


def test_recovery_level_progression():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    sig = StableSignature.from_tool_call("bash", '{"command":"ls"}')
    for _ in range(3):
        det.record_round(text="", tool_calls=[sig])
    assert det.check_stall() in (RecoveryLevel.NONE, RecoveryLevel.MILD)
    for _ in range(4):
        det.record_round(text="", tool_calls=[sig])
    assert det.check_stall() == RecoveryLevel.STRONG


def test_reset_clears_state():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    sig = StableSignature.from_tool_call("bash", '{"command":"ls"}')
    for _ in range(5):
        det.record_round(text="", tool_calls=[sig])
    det.reset()
    assert det.is_runaway() is False
    assert det.check_stall() == RecoveryLevel.NONE
    assert det.check_text_loop() == RecoveryLevel.NONE


def test_round_count():
    det = LoopDetector(max_rounds=12, stall_threshold=4, runaway_threshold=15)
    assert det.round_count == 0
    det.record_round(text="hello", tool_calls=[])
    assert det.round_count == 1
    det.record_round(text="world", tool_calls=[])
    assert det.round_count == 2
