# ============================================================
# tests/test_rcaf_canonical_store.py
# ============================================================

from __future__ import annotations

import importlib.util
import json
import multiprocessing
import os
import stat
from dataclasses import replace
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
FIXTURE_PATH = (
    REPO_ROOT
    / "tests/test_rcaf_canonical_ledger.py"
)


def _load_full_record():
    spec = importlib.util.spec_from_file_location(
        "rcaf_canonical_test_fixture",
        FIXTURE_PATH,
    )

    if (
        spec is None
        or spec.loader is None
    ):
        raise RuntimeError(
            "unable to load canonical fixture"
        )

    module = importlib.util.module_from_spec(
        spec
    )

    spec.loader.exec_module(module)

    return module._full_record()


def _record(
    index: int,
):
    return replace(
        _load_full_record(),
        record_id=(
            f"RCAF-FULL-{index:04d}"
        ),
        event_spine_id=(
            f"SPINE-{index:04d}"
        ),
    )


def _append_worker(
    root: str,
    index: int,
) -> None:
    append_canonical_record(
        root,
        ledger_id="TEST-LEDGER",
        record=_record(index),
        max_records_per_segment=2,
    )


def _segment_paths(
    root: Path,
) -> list[Path]:
    return sorted(
        root.glob(
            "segment-*.jsonl"
        )
    )


def test_append_rotate_validate_and_permissions(
    tmp_path,
):
    root = tmp_path / "ledger"

    receipts = [
        append_canonical_record(
            root,
            ledger_id="TEST-LEDGER",
            record=_record(index),
            max_records_per_segment=2,
        )
        for index in range(1, 6)
    ]

    assert [
        receipt.sequence_index
        for receipt in receipts
    ] == [0, 1, 2, 3, 4]

    assert [
        receipt.segment_index
        for receipt in receipts
    ] == [0, 0, 1, 1, 2]

    replay = (
        validate_and_replay_canonical_store(
            root,
            ledger_id="TEST-LEDGER",
        )
    )

    assert replay.record_count == 5
    assert replay.segment_count == 3
    assert replay.last_sequence_index == 4
    assert replay.integrity_verified is True
    assert (
        replay.deterministic_replay_verified
        is True
    )

    assert (
        stat.S_IMODE(
            os.stat(root).st_mode
        )
        == 0o700
    )

    for path in (
        _segment_paths(root)
        + [
            root / "manifest.json",
            root / ".lock",
        ]
    ):
        assert (
            stat.S_IMODE(
                os.stat(path).st_mode
            )
            == 0o600
        )


def test_hash_chain_crosses_segment_boundary(
    tmp_path,
):
    root = tmp_path / "ledger"

    for index in range(1, 4):
        append_canonical_record(
            root,
            ledger_id="TEST-LEDGER",
            record=_record(index),
            max_records_per_segment=2,
        )

    first_segment = [
        json.loads(line)
        for line in (
            root
            / "segment-000000.jsonl"
        ).read_text().splitlines()
    ]

    second_segment = [
        json.loads(line)
        for line in (
            root
            / "segment-000001.jsonl"
        ).read_text().splitlines()
    ]

    assert (
        second_segment[0][
            "previous_entry_sha256"
        ]
        == first_segment[-1][
            "entry_sha256"
        ]
    )


def test_trailing_partial_write_is_recovered(
    tmp_path,
):
    root = tmp_path / "ledger"

    append_canonical_record(
        root,
        ledger_id="TEST-LEDGER",
        record=_record(1),
    )

    segment = (
        root / "segment-000000.jsonl"
    )

    with segment.open("ab") as handle:
        handle.write(
            b'{"schema":"interrupted'
        )
        handle.flush()
        os.fsync(handle.fileno())

    replay = (
        validate_and_replay_canonical_store(
            root,
            ledger_id="TEST-LEDGER",
            recover_trailing=True,
        )
    )

    assert replay.record_count == 1
    assert (
        replay.recovered_trailing_bytes
        > 0
    )

    receipt = append_canonical_record(
        root,
        ledger_id="TEST-LEDGER",
        record=_record(2),
    )

    assert receipt.sequence_index == 1

    final = (
        validate_and_replay_canonical_store(
            root,
            ledger_id="TEST-LEDGER",
        )
    )

    assert final.record_count == 2


def test_valid_terminal_record_without_newline_is_repaired(
    tmp_path,
):
    root = tmp_path / "ledger"

    append_canonical_record(
        root,
        ledger_id="TEST-LEDGER",
        record=_record(1),
    )

    segment = (
        root / "segment-000000.jsonl"
    )

    data = segment.read_bytes()

    assert data.endswith(b"\n")

    segment.write_bytes(
        data[:-1]
    )

    replay = (
        validate_and_replay_canonical_store(
            root,
            ledger_id="TEST-LEDGER",
            recover_trailing=True,
        )
    )

    assert (
        replay.repaired_terminal_newline
        is True
    )
    assert segment.read_bytes().endswith(
        b"\n"
    )


def test_interior_corruption_is_rejected(
    tmp_path,
):
    root = tmp_path / "ledger"

    for index in range(1, 3):
        append_canonical_record(
            root,
            ledger_id="TEST-LEDGER",
            record=_record(index),
        )

    segment = (
        root / "segment-000000.jsonl"
    )

    lines = segment.read_bytes().splitlines(
        keepends=True
    )

    lines[0] = lines[0].replace(
        b'"sequence_index":0',
        b'"sequence_index":9',
        1,
    )

    segment.write_bytes(
        b"".join(lines)
    )

    with pytest.raises(
        CanonicalStoreIntegrityError,
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="TEST-LEDGER",
            recover_trailing=True,
        )


def test_manifest_is_reconstructed_from_segments(
    tmp_path,
):
    root = tmp_path / "ledger"

    for index in range(1, 4):
        append_canonical_record(
            root,
            ledger_id="TEST-LEDGER",
            record=_record(index),
            max_records_per_segment=2,
        )

    manifest = root / "manifest.json"

    manifest.unlink()

    replay = (
        validate_and_replay_canonical_store(
            root,
            ledger_id="TEST-LEDGER",
            rebuild_manifest=True,
        )
    )

    assert replay.record_count == 3
    assert manifest.exists()

    rebuilt = json.loads(
        manifest.read_text()
    )

    assert rebuilt["record_count"] == 3
    assert rebuilt["segment_count"] == 2
    assert (
        rebuilt["last_entry_sha256"]
        == replay.last_entry_sha256
    )


def test_concurrent_process_writers_are_serialized(
    tmp_path,
):
    root = tmp_path / "ledger"

    processes = [
        multiprocessing.Process(
            target=_append_worker,
            args=(
                str(root),
                index,
            ),
        )
        for index in range(1, 7)
    ]

    for process in processes:
        process.start()

    for process in processes:
        process.join(timeout=20)

        assert process.exitcode == 0

    replay = (
        validate_and_replay_canonical_store(
            root,
            ledger_id="TEST-LEDGER",
        )
    )

    assert replay.record_count == 6
    assert replay.segment_count == 3

    record_ids = {
        record["record_id"]
        for record in replay.records
    }

    assert len(record_ids) == 6


def test_duplicate_record_id_is_rejected(
    tmp_path,
):
    root = tmp_path / "ledger"

    record = _record(1)

    append_canonical_record(
        root,
        ledger_id="TEST-LEDGER",
        record=record,
    )

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="duplicate canonical record_id",
    ):
        append_canonical_record(
            root,
            ledger_id="TEST-LEDGER",
            record=record,
        )


def test_symlink_segment_is_rejected(
    tmp_path,
):
    root = tmp_path / "ledger"
    root.mkdir(mode=0o700)

    target = tmp_path / "target"
    target.write_text("unsafe")

    (
        root / "segment-000000.jsonl"
    ).symlink_to(target)

    with pytest.raises(
        CanonicalStoreSecurityError,
    ):
        validate_and_replay_canonical_store(
            root,
            ledger_id="TEST-LEDGER",
        )


def test_store_contains_no_private_marker(
    tmp_path,
):
    marker = (
        "PRIVATE-RCAF-MARKER-"
        "8af7e5a47ad14cbd9a4e4df3f952f06c"
    )

    root = tmp_path / "ledger"

    append_canonical_record(
        root,
        ledger_id="TEST-LEDGER",
        record=_record(1),
    )

    combined = b"".join(
        path.read_bytes()
        for path in root.iterdir()
        if path.is_file()
    )

    assert marker.encode() not in combined

    replay = (
        validate_and_replay_canonical_store(
            root,
            ledger_id="TEST-LEDGER",
        )
    )

    assert replay.authority_posture == (
        "observe_only"
    )



def test_store_rejects_missing_future_authority_bundle():
    record = _record(1).to_dict()

    record.pop(
        "future_authority_evidence"
    )

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="lacks the required future-authority",
    ):
        _validate_framework_record_dict(
            record
        )


def test_store_rejects_observe_only_future_authority_eligibility():
    record = _record(1).to_dict()

    record[
        "future_authority_evidence"
    ][
        "causal_authority_eligible"
    ] = True

    with pytest.raises(
        CanonicalStoreIntegrityError,
        match="cannot be future-authority eligible",
    ):
        _validate_framework_record_dict(
            record
        )
