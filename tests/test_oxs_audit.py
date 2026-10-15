"""Tests for OXS audit log (file + DB mirror)."""

from __future__ import annotations

import json
import os
import tempfile

import pytest

from core.extensions import AuditEvent, AuditSink, audit_log, audit_tail


# ── AuditEvent ────────────────────────────────────────────────────────────


def test_audit_event_serializes_to_json_line():
    ev = AuditEvent(
        event="extension.install",
        actor="admin",
        extension_id="hello-odysseus",
        payload={"version": "1.0.0"},
    )
    line = ev.to_json_line()
    parsed = json.loads(line)
    assert parsed["event"] == "extension.install"
    assert parsed["actor"] == "admin"
    assert parsed["extension_id"] == "hello-odysseus"
    assert parsed["payload"] == {"version": "1.0.0"}
    assert "timestamp" in parsed


def test_audit_event_with_null_extension():
    ev = AuditEvent(event="oxs.startup", actor="system", extension_id=None)
    line = ev.to_json_line()
    parsed = json.loads(line)
    assert parsed["extension_id"] is None


def test_audit_event_timestamp_is_iso_utc():
    ev = AuditEvent(event="x", actor="y", extension_id=None)
    assert ev.timestamp.endswith("Z") or "+" in ev.timestamp


# ── AuditSink ─────────────────────────────────────────────────────────────


def test_audit_sink_appends_file():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "audit.log")
        sink = AuditSink(log_path=path, db_session_factory=None)
        sink.write(AuditEvent(event="e1", actor="system", extension_id=None))
        sink.write(AuditEvent(event="e2", actor="admin", extension_id="ext1", payload={"k": 1}))

        assert os.path.exists(path)
        with open(path) as f:
            lines = f.read().strip().split("\n")
        assert len(lines) == 2
        assert json.loads(lines[0])["event"] == "e1"
        assert json.loads(lines[1])["event"] == "e2"
        assert json.loads(lines[1])["payload"] == {"k": 1}


def test_audit_sink_creates_parent_directory():
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "deep", "nested", "audit.log")
        sink = AuditSink(log_path=path, db_session_factory=None)
        sink.write(AuditEvent(event="e1", actor="system", extension_id=None))
        assert os.path.exists(path)


def test_audit_sink_appends_preserves_existing():
    """Audit log is append-only — never truncate or rewrite."""
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "audit.log")
        sink = AuditSink(log_path=path, db_session_factory=None)

        sink.write(AuditEvent(event="first", actor="system", extension_id=None))
        # Simulate external rotation by truncating and reopening
        with open(path, "w") as f:
            f.write("MANUALLY OVERWRITTEN\n")
        sink.write(AuditEvent(event="second", actor="admin", extension_id="x"))

        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 2
        assert "MANUALLY OVERWRITTEN" in lines[0]
        assert "second" in lines[1]


# ── Module-level helpers ──────────────────────────────────────────────────


def test_audit_log_helper_writes_through_sink(monkeypatch):
    """``audit_log`` should use the module-level sink."""
    with tempfile.TemporaryDirectory() as d:
        from core.extensions import audit as audit_mod
        path = os.path.join(d, "audit.log")
        monkeypatch.setattr(audit_mod, "OXS_AUDIT_LOG", path)
        monkeypatch.setattr(audit_mod, "_DEFAULT_SINK",
                            AuditSink(log_path=path, db_session_factory=None))

        audit_log("extension.test", extension_id="ext1", actor="admin", payload={"k": "v"})

        events = audit_tail(10)
        assert len(events) == 1
        assert events[0]["event"] == "extension.test"
        assert events[0]["extension_id"] == "ext1"
        assert events[0]["payload"] == {"k": "v"}


def test_audit_tail_returns_empty_when_no_file(tmp_path):
    from core.extensions import audit as audit_mod
    monkey_log = tmp_path / "audit.log"
    # Point to a path that does not exist.
    monkeypatch_path = str(tmp_path / "does-not-exist.log")
    # Audit_tail reads from OXS_AUDIT_LOG, so swap it temporarily.
    import core.extensions.audit as _audit
    original = _audit.OXS_AUDIT_LOG
    _audit.OXS_AUDIT_LOG = monkeypatch_path
    try:
        assert _audit.audit_tail(10) == []
    finally:
        _audit.OXS_AUDIT_LOG = original


def test_audit_tail_skips_malformed_lines(tmp_path):
    """Forward-compat: old logs may have lines we cannot parse. Skip, don't crash."""
    from core.extensions import audit as audit_mod
    log_path = tmp_path / "audit.log"
    log_path.write_text(
        '{"event":"good","actor":"system","extension_id":null,"payload":{},"timestamp":"x"}\n'
        'this is not json\n'
        '{"event":"also-good","actor":"system","extension_id":null,"payload":{},"timestamp":"y"}\n'
    )
    original = audit_mod.OXS_AUDIT_LOG
    audit_mod.OXS_AUDIT_LOG = str(log_path)
    try:
        events = audit_mod.audit_tail(10)
        assert len(events) == 2
        assert events[0]["event"] == "good"
        assert events[1]["event"] == "also-good"
    finally:
        audit_mod.OXS_AUDIT_LOG = original


def test_audit_tail_caps_at_n(tmp_path):
    from core.extensions import audit as audit_mod
    log_path = tmp_path / "audit.log"
    lines = []
    for i in range(50):
        lines.append(json.dumps({
            "event": f"e{i}",
            "actor": "system",
            "extension_id": None,
            "payload": {},
            "timestamp": "x",
        }))
    log_path.write_text("\n".join(lines) + "\n")

    original = audit_mod.OXS_AUDIT_LOG
    audit_mod.OXS_AUDIT_LOG = str(log_path)
    try:
        events = audit_mod.audit_tail(10)
        assert len(events) == 10
        # Newest-last ordering: last 10 of 0..49 = 40..49
        assert events[0]["event"] == "e40"
        assert events[-1]["event"] == "e49"
    finally:
        audit_mod.OXS_AUDIT_LOG = original


# ── Concurrency ───────────────────────────────────────────────────────────


def test_audit_log_is_thread_safe():
    """Many threads writing simultaneously should not interleave or corrupt."""
    import threading
    with tempfile.TemporaryDirectory() as d:
        path = os.path.join(d, "audit.log")
        sink = AuditSink(log_path=path, db_session_factory=None)
        n_per = 50
        threads = []
        for tid in range(8):
            def worker(t=tid):
                for i in range(n_per):
                    sink.write(AuditEvent(
                        event=f"e.{t}.{i}",
                        actor=f"thread-{t}",
                        extension_id="x",
                        payload={"i": i, "t": t},
                    ))
            threads.append(threading.Thread(target=worker))
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        with open(path) as f:
            lines = f.readlines()
        assert len(lines) == 8 * n_per
        # Every line must be valid JSON
        for line in lines:
            json.loads(line)