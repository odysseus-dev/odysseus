# ============================================================
# tests/test_rcaf_canonical_store_fault_injection.py
# ============================================================

from __future__ import annotations

import importlib.util
import json
import os
import stat
from pathlib import Path

import pytest

from src.rcaf.canonical_store import (
    CanonicalStoreIntegrityError,
    CanonicalStoreSecurityError,
    _validate_framework_record_dict,
    append_canonical_record,
    validate_and_replay_canonical_store,
)


REPO_ROOT = Path(__file__).resolve().parents[1]

STORE_FIXTURE_PATH = (
    REPO_ROOT
    / "tests/test_rcaf_canonical_store.py"
)


def _load_store_fixture_module():
    spec = importlib.util.spec_from_file_location(
        "rcaf_canonical_store_test_fixture",
        STORE_FIXTURE_PATH,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "unable to load canonical-store fixture"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(module)

    return module


def _record(
    index: int,
):
    return _load_store_fixture_module()._record(
        index
    )


def _append(
    root: Path,
    index: int,
    *,
    max_records_per_segment: int = 2,
):
    return append_canonical_record(
        root,
        ledger_id="FAULT-LEDGER",
        record=_record(index),
        max_records_per_segment=(
            max_records_per_segment
        ),
    )


def test_root_symlink_is_rejected(
    tmp_path,
):
    target = tmp_path / "real-ledger"
    target.mkdir(mode=0o700)

    root = tmp_path / "ledger-link"
    root.symlink_to(
        target,
        target_is_directory=True,
    )

    with pytest.raises(
        CanonicalStoreSecurityError,
        match="must not be a symbolic link",
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="FAULT-LEDGER",
        )


def test_hard_linked_segment_is_rejected(
    tmp_path,
):
    root = tmp_path / "ledger"

    _append(root, 1)

    segment = (
        root / "segment-000000.jsonl"
    )

    os.link(
        segment,
        tmp_path / "segment-hard-link",
    )

    with pytest.raises(
        CanonicalStoreSecurityError,
        match="exactly one link",
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="FAULT-LEDGER",
        )


def test_non_contiguous_segments_are_rejected(
    tmp_path,
):
    root = tmp_path / "ledger"
    root.mkdir(mode=0o700)

    (
        root / "segment-000001.jsonl"
    ).write_bytes(b"")

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="contiguous from zero",
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="FAULT-LEDGER",
        )


def test_nonterminal_partial_write_is_not_recovered(
    tmp_path,
):
    root = tmp_path / "ledger"

    for index in range(1, 4):
        _append(
            root,
            index,
            max_records_per_segment=2,
        )

    first_segment = (
        root / "segment-000000.jsonl"
    )

    with first_segment.open("ab") as handle:
        handle.write(
            b'{"schema":"interrupted'
        )
        handle.flush()
        os.fsync(handle.fileno())

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="invalid trailing partial record",
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="FAULT-LEDGER",
            recover_trailing=True,
        )


def test_stale_manifest_is_reconstructed(
    tmp_path,
):
    root = tmp_path / "ledger"

    _append(root, 1)
    _append(root, 2)

    manifest = root / "manifest.json"

    manifest.write_text(
        json.dumps(
            {
                "schema": "STALE-MANIFEST",
                "ledger_id": "WRONG",
                "record_count": 999,
                "segment_count": 999,
                "last_entry_sha256": "f" * 64,
            }
        ),
        encoding="utf-8",
    )

    manifest.chmod(0o600)

    replay = (
        validate_and_replay_canonical_store(
            root,
            ledger_id="FAULT-LEDGER",
            rebuild_manifest=True,
        )
    )

    rebuilt = json.loads(
        manifest.read_text(
            encoding="utf-8"
        )
    )

    assert replay.record_count == 2
    assert rebuilt["ledger_id"] == (
        "FAULT-LEDGER"
    )
    assert rebuilt["record_count"] == 2
    assert rebuilt["segment_count"] == 1
    assert (
        rebuilt["last_entry_sha256"]
        == replay.last_entry_sha256
    )


def test_empty_terminal_segment_after_rotation_crash_is_reused(
    tmp_path,
):
    root = tmp_path / "ledger"

    _append(
        root,
        1,
        max_records_per_segment=2,
    )

    _append(
        root,
        2,
        max_records_per_segment=2,
    )

    empty_segment = (
        root / "segment-000001.jsonl"
    )

    empty_segment.write_bytes(b"")
    empty_segment.chmod(0o600)

    interrupted = (
        validate_and_replay_canonical_store(
            root,
            ledger_id="FAULT-LEDGER",
            rebuild_manifest=True,
        )
    )

    assert interrupted.record_count == 2
    assert interrupted.segment_count == 2

    receipt = _append(
        root,
        3,
        max_records_per_segment=2,
    )

    assert receipt.sequence_index == 2
    assert receipt.segment_index == 1

    final = (
        validate_and_replay_canonical_store(
            root,
            ledger_id="FAULT-LEDGER",
        )
    )

    assert final.record_count == 3
    assert final.segment_count == 2


def test_record_payload_hash_tampering_is_rejected(
    tmp_path,
):
    root = tmp_path / "ledger"

    _append(root, 1)

    segment = (
        root / "segment-000000.jsonl"
    )

    entries = [
        json.loads(line)
        for line in segment.read_text(
            encoding="utf-8"
        ).splitlines()
    ]

    entries[0]["record"]["record_id"] = (
        "RCAF-TAMPERED-RECORD"
    )

    segment.write_text(
        "\n".join(
            json.dumps(
                entry,
                sort_keys=True,
                separators=(",", ":"),
            )
            for entry in entries
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="entry SHA-256 mismatch",
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="FAULT-LEDGER",
        )


def test_previous_hash_tampering_is_rejected(
    tmp_path,
):
    root = tmp_path / "ledger"

    _append(root, 1)
    _append(root, 2)

    segment = (
        root / "segment-000000.jsonl"
    )

    entries = [
        json.loads(line)
        for line in segment.read_text(
            encoding="utf-8"
        ).splitlines()
    ]

    entries[1][
        "previous_entry_sha256"
    ] = "0" * 64

    segment.write_text(
        "\n".join(
            json.dumps(
                entry,
                sort_keys=True,
                separators=(",", ":"),
            )
            for entry in entries
        )
        + "\n",
        encoding="utf-8",
    )

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="hash-chain continuity failure",
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="FAULT-LEDGER",
        )


def test_ledger_id_mismatch_is_rejected(
    tmp_path,
):
    root = tmp_path / "ledger"

    _append(root, 1)

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="entry ledger_id mismatch",
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="OTHER-LEDGER",
        )


def test_validation_repairs_private_permissions(
    tmp_path,
):
    root = tmp_path / "ledger"

    _append(root, 1)

    root.chmod(0o777)

    for path in (
        root / "segment-000000.jsonl",
        root / "manifest.json",
        root / ".lock",
    ):
        path.chmod(0o666)

    validate_and_replay_canonical_store(
        root,
        ledger_id="FAULT-LEDGER",
        rebuild_manifest=True,
    )

    assert (
        stat.S_IMODE(
            os.stat(root).st_mode
        )
        == 0o700
    )

    for path in (
        root / "segment-000000.jsonl",
        root / "manifest.json",
        root / ".lock",
    ):
        assert (
            stat.S_IMODE(
                os.stat(path).st_mode
            )
            == 0o600
        )


def test_unsupported_canonical_schema_is_rejected():
    record = _record(1).to_dict()

    record["schema"] = (
        "RCAF-CANONICAL-FRAMEWORK-LEDGER-0.2"
    )

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="record schema mismatch",
    ):
        _validate_framework_record_dict(
            record
        )


def test_unsupported_future_authority_schema_is_rejected():
    record = _record(1).to_dict()

    record[
        "future_authority_evidence"
    ][
        "schema"
    ] = (
        "RCAF-FUTURE-CONDITIONED-AUTHORITY-UNKNOWN"
    )

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="lacks the required future-authority",
    ):
        _validate_framework_record_dict(
            record
        )


def test_incomplete_canonical_record_is_rejected():
    record = _record(1).to_dict()

    record["completeness"][
        "complete"
    ] = False

    record["completeness"][
        "missing_capabilities"
    ] = [
        "matched_causal_branching"
    ]

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="canonical record is not complete",
    ):
        _validate_framework_record_dict(
            record
        )


def test_unsafe_manifest_symlink_is_rejected(
    tmp_path,
):
    root = tmp_path / "ledger"

    _append(root, 1)

    manifest = root / "manifest.json"
    manifest.unlink()

    target = tmp_path / "outside-manifest"
    target.write_text(
        "{}",
        encoding="utf-8",
    )

    manifest.symlink_to(target)

    with pytest.raises(
        CanonicalStoreSecurityError,
        match="existing manifest is unsafe",
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="FAULT-LEDGER",
            rebuild_manifest=True,
        )
