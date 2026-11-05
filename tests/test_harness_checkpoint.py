"""Files are copied before a rewrite and can be restored."""
import json

from src import harness_checkpoint


def _ctx(owner="ada", session="s1", turn="t1"):
    return {"owner": owner, "session_id": session, "request_id": turn}


def _manifest(owner="ada", session="s1", turn="t1"):
    bucket = harness_checkpoint._root(owner, session) / harness_checkpoint._slug(turn)
    return json.loads((bucket / "manifest.json").read_text(encoding="utf-8")), bucket


def test_snapshot_and_rewind_restores_bytes(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_checkpoint, "AGENT_WORKSPACE_DIR", str(tmp_path))
    target = tmp_path / "note.txt"
    target.write_text("before", encoding="utf-8")
    assert harness_checkpoint.snapshot_existing(str(target), _ctx())
    target.write_text("after", encoding="utf-8")
    result = harness_checkpoint.rewind_latest("ada", "s1")
    assert result["ok"] is True
    assert result["restored"] == [str(target.resolve())]
    assert target.read_text(encoding="utf-8") == "before"


def test_missing_file_is_not_snapshotted(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_checkpoint, "AGENT_WORKSPACE_DIR", str(tmp_path))
    assert harness_checkpoint.snapshot_existing(str(tmp_path / "nope.txt"), _ctx()) is None
    assert harness_checkpoint.rewind_latest("ada", "s1")["ok"] is False


def test_rewind_without_a_checkpoint_reports_it(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_checkpoint, "AGENT_WORKSPACE_DIR", str(tmp_path))
    result = harness_checkpoint.rewind_latest("nobody", "s1")
    assert result["ok"] is False
    assert "no checkpoint" in result["error"]
    assert result["restored"] == []


def test_same_file_in_one_turn_is_snapshotted_once(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_checkpoint, "AGENT_WORKSPACE_DIR", str(tmp_path))
    target = tmp_path / "note.txt"
    target.write_text("v1", encoding="utf-8")
    harness_checkpoint.snapshot_existing(str(target), _ctx())
    target.write_text("v2", encoding="utf-8")
    harness_checkpoint.snapshot_existing(str(target), _ctx())
    manifest, _bucket = _manifest()
    assert len(manifest) == 1


def test_rewind_restores_every_file_in_the_turn(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_checkpoint, "AGENT_WORKSPACE_DIR", str(tmp_path))
    files = [tmp_path / "a.txt", tmp_path / "b.txt"]
    for index, path in enumerate(files):
        path.write_text(f"original-{index}", encoding="utf-8")
        harness_checkpoint.snapshot_existing(str(path), _ctx())
        path.write_text("changed", encoding="utf-8")
    result = harness_checkpoint.rewind_latest("ada", "s1")
    assert result["ok"] is True
    assert sorted(result["restored"]) == sorted(str(p.resolve()) for p in files)
    assert [p.read_text(encoding="utf-8") for p in files] == ["original-0", "original-1"]


def test_crafted_blob_path_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_checkpoint, "AGENT_WORKSPACE_DIR", str(tmp_path))
    target = tmp_path / "note.txt"
    target.write_text("safe", encoding="utf-8")
    harness_checkpoint.snapshot_existing(str(target), _ctx())
    manifest, bucket = _manifest()
    manifest[0]["blob"] = "../evil.bin"
    (bucket / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    target.write_text("changed", encoding="utf-8")

    result = harness_checkpoint.rewind_latest("ada", "s1")

    assert result["ok"] is True
    assert result["restored"] == []
    assert result["skipped"] and "invalid manifest entry" in result["skipped"][0]["reason"]
    assert target.read_text(encoding="utf-8") == "changed"


def test_blob_symlink_outside_the_bucket_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_checkpoint, "AGENT_WORKSPACE_DIR", str(tmp_path))
    target = tmp_path / "note.txt"
    target.write_text("safe", encoding="utf-8")
    harness_checkpoint.snapshot_existing(str(target), _ctx())
    manifest_data, bucket = _manifest()
    outside = tmp_path / "evil.bin"
    outside.write_text("evil", encoding="utf-8")
    blob = bucket / "0000.bin"
    blob.unlink()
    blob.symlink_to(outside)
    target.write_text("changed", encoding="utf-8")

    result = harness_checkpoint.rewind_latest("ada", "s1")

    assert result["ok"] is True
    assert result["restored"] == []
    assert manifest_data and "outside the checkpoint" in result["skipped"][0]["reason"]
    assert target.read_text(encoding="utf-8") == "changed"


def test_protected_destination_is_skipped(tmp_path, monkeypatch):
    monkeypatch.setattr(harness_checkpoint, "AGENT_WORKSPACE_DIR", str(tmp_path))
    target = tmp_path / "note.txt"
    target.write_text("before", encoding="utf-8")
    harness_checkpoint.snapshot_existing(str(target), _ctx())
    target.write_text("after", encoding="utf-8")
    monkeypatch.setattr(
        harness_checkpoint,
        "_dest_blocked",
        lambda dest: "StateGate: rewind blocked on protected path /etc",
    )

    result = harness_checkpoint.rewind_latest("ada", "s1")

    assert result["ok"] is True
    assert result["restored"] == []
    assert result["skipped"][0]["path"] == str(target.resolve())
    assert target.read_text(encoding="utf-8") == "after"
