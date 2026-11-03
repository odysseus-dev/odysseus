"""Deterministic tests for audit_c5_benchmark_evidence.py.

All tests use temporary synthetic fixtures - no network, no runtime, no model.
Tests are self-contained and safe to run repeatedly without changing source data.

GFM integration checks are separate from the dependency-free unit suite:
    python -m venv /tmp/c5-gfm
    /tmp/c5-gfm/bin/pip install cmarkgfm==2025.10.22
    /tmp/c5-gfm/bin/python tests/test_audit_c5_benchmark_evidence.py --gfm
The explicit --gfm command fails if cmarkgfm is unavailable; it never skips.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path

# Ensure project root is on path for import without conftest.
_ROOT = Path(__file__).resolve().parents[1]
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import scripts.audit_c5_benchmark_evidence as auditor


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _write(path: Path, content: bytes) -> str:
    """Write bytes to path and return its sha256."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
    return _sha256(content)


def _manifest(artifacts: list, campaign_id: str = "TEST-CAMPAIGN-01", expected_sets: dict | None = None) -> dict:
    d = {
        "schema_version": 1,
        "campaign_id": campaign_id,
        "description": "Synthetic test manifest",
        "artifacts": artifacts,
    }
    if expected_sets:
        d["expected_sets"] = expected_sets
    return d


def _run_audit(
    tmp: Path,
    manifest_dict: dict,
    deterministic: bool = False,
    trusted_sources: list[str] | None = None,
    trusted_expected_sets: dict | None = None,
) -> dict:
    """Write manifest to tmp, run audit, return parsed result."""
    mpath = tmp / "manifest.json"
    mpath.write_text(json.dumps(manifest_dict))
    return auditor.audit(
        str(mpath),
        str(tmp),
        deterministic=deterministic,
        trusted_sources=trusted_sources,
        trusted_expected_sets=trusted_expected_sets,
    )


# ---------------------------------------------------------------------------
# Baseline Integrity and Safety Tests
# ---------------------------------------------------------------------------

class TestValidMatchingHash(unittest.TestCase):
    """VERIFIED: artifact present, observed hash matches expected hash."""

    def test_verified_when_observed_matches_expected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b"hello world"
            h = _write(tmp / "file.txt", content)
            result = _run_audit(tmp, _manifest([{
                "path": "file.txt",
                "type": "result_record",
                "expected_sha256": h,
            }]))
            art = result["artifacts"][0]
            self.assertEqual(art["status"], "VERIFIED")
            self.assertTrue(art["present"])
            self.assertEqual(art["observed_sha256"], h)
            self.assertEqual(art["expected_sha256"], h)
            self.assertTrue(art["hash_match"])
            self.assertEqual(result["totals"]["VERIFIED"], 1)


class TestMismatchedHash(unittest.TestCase):
    """MISMATCH: artifact present, but observed hash differs from expected."""

    def test_mismatch_when_hash_differs(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b"actual content"
            _write(tmp / "data.json", content)
            wrong_hash = _sha256(b"different content")
            result = _run_audit(tmp, _manifest([{
                "path": "data.json",
                "type": "campaign_manifest",
                "expected_sha256": wrong_hash,
            }]))
            art = result["artifacts"][0]
            self.assertEqual(art["status"], "MISMATCH")
            self.assertTrue(art["present"])
            self.assertNotEqual(art["observed_sha256"], art["expected_sha256"])
            self.assertFalse(art["hash_match"])
            self.assertEqual(result["totals"]["MISMATCH"], 1)


class TestMissingArtifact(unittest.TestCase):
    """MISSING: expected artifact is absent from the filesystem."""

    def test_missing_when_file_absent(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = _run_audit(tmp, _manifest([{
                "path": "nonexistent/file.json",
                "type": "trajectory",
                "expected_sha256": "a" * 64,
            }]))
            art = result["artifacts"][0]
            self.assertEqual(art["status"], "MISSING")
            self.assertFalse(art["present"])
            self.assertIsNone(art["observed_sha256"])
            self.assertIsNone(art["size_bytes"])
            self.assertEqual(result["totals"]["MISSING"], 1)


class TestObservedOnlyNoExpectedHash(unittest.TestCase):
    """OBSERVED_ONLY: artifact exists but manifest has no expected hash."""

    def test_observed_only_when_no_expected_hash(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write(tmp / "report.md", b"# Report\n")
            result = _run_audit(tmp, _manifest([{
                "path": "report.md",
                "type": "result_record",
            }]))
            art = result["artifacts"][0]
            self.assertEqual(art["status"], "OBSERVED_ONLY")
            self.assertTrue(art["present"])
            self.assertIsNotNone(art["observed_sha256"])
            self.assertIsNone(art["expected_sha256"])
            self.assertFalse(art["hash_match"])
            self.assertEqual(result["totals"]["OBSERVED_ONLY"], 1)

    def test_observed_only_when_expected_hash_is_null(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write(tmp / "report.md", b"# Report\n")
            result = _run_audit(tmp, _manifest([{
                "path": "report.md",
                "type": "result_record",
                "expected_sha256": None,
            }]))
            art = result["artifacts"][0]
            self.assertEqual(art["status"], "OBSERVED_ONLY")


class TestInvalidManifest(unittest.TestCase):
    """INVALID: manifest fails validation."""

    def test_invalid_missing_schema_version(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps({"campaign_id": "X", "artifacts": []}))
            result = auditor.audit(str(mpath), str(tmp))
            self.assertFalse(result["manifest_valid"])
            self.assertGreater(len(result["errors"]), 0)

    def test_invalid_wrong_schema_version(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = _run_audit(tmp, {"schema_version": 99, "campaign_id": "X", "artifacts": []})
            self.assertFalse(result["manifest_valid"])

    def test_invalid_artifacts_not_a_list(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = _run_audit(tmp, {"schema_version": 1, "campaign_id": "X", "artifacts": "oops"})
            self.assertFalse(result["manifest_valid"])

    def test_invalid_malformed_json(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text("{not valid json}")
            result = auditor.audit(str(mpath), str(tmp))
            self.assertFalse(result["manifest_valid"])
            self.assertGreater(len(result["errors"]), 0)

    def test_invalid_missing_manifest_file(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "does_not_exist.json"
            result = auditor.audit(str(mpath), str(tmp))
            self.assertFalse(result["manifest_valid"])
            self.assertGreater(len(result["errors"]), 0)


class TestPathTraversal(unittest.TestCase):
    """INVALID: path traversal attempts must be rejected."""

    def test_reject_dotdot_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = _run_audit(tmp, _manifest([{
                "path": "../escape.txt",
                "type": "result_record",
            }]))
            art = result["artifacts"][0]
            self.assertEqual(art["status"], "INVALID")

    def test_reject_absolute_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = _run_audit(tmp, _manifest([{
                "path": "/etc/passwd",
                "type": "result_record",
            }]))
            art = result["artifacts"][0]
            self.assertEqual(art["status"], "INVALID")

    def test_reject_nested_dotdot(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = _run_audit(tmp, _manifest([{
                "path": "subdir/../../etc/passwd",
                "type": "result_record",
            }]))
            art = result["artifacts"][0]
            self.assertEqual(art["status"], "INVALID")


class TestSymlinkEscape(unittest.TestCase):
    """UNVERIFIABLE: symlinks that escape the designated root."""

    def test_symlink_escape_is_unverifiable(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            link = tmp / "escape_link.txt"
            link.symlink_to("/etc/hostname")
            result = _run_audit(tmp, _manifest([{
                "path": "escape_link.txt",
                "type": "result_record",
            }]))
            art = result["artifacts"][0]
            self.assertIn(art["status"], ("UNVERIFIABLE", "INVALID"))

    def test_internal_symlink_is_handled(self):
        """A symlink that stays within root is rejected as UNVERIFIABLE under O_NOFOLLOW boundary."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            target = tmp / "real.txt"
            target.write_bytes(b"data")
            link = tmp / "link.txt"
            link.symlink_to(target)
            result = _run_audit(tmp, _manifest([{
                "path": "link.txt",
                "type": "result_record",
            }]))
            art = result["artifacts"][0]
            self.assertNotEqual(art["status"], "INVALID")
            self.assertEqual(art["status"], "UNVERIFIABLE")


class TestStableRepeatedOutput(unittest.TestCase):
    """Stable: running the audit twice must produce identical output."""

    def test_identical_output_on_repeated_runs(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b"stable content"
            h = _write(tmp / "a.json", content)
            _write(tmp / "b.json", b"other")
            manifest = _manifest([
                {"path": "a.json", "type": "result_record", "expected_sha256": h},
                {"path": "b.json", "type": "campaign_manifest"},
                {"path": "missing.json", "type": "trajectory", "expected_sha256": "b" * 64},
            ])
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(manifest))

            r1 = auditor.audit(str(mpath), str(tmp), deterministic=True)
            r2 = auditor.audit(str(mpath), str(tmp), deterministic=True)

            self.assertEqual(
                json.dumps(r1, sort_keys=True),
                json.dumps(r2, sort_keys=True),
                "Repeated runs produced different output",
            )

    def test_artifacts_sorted_by_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write(tmp / "zzz.txt", b"z")
            _write(tmp / "aaa.txt", b"a")
            result = _run_audit(tmp, _manifest([
                {"path": "zzz.txt", "type": "result_record"},
                {"path": "aaa.txt", "type": "result_record"},
            ]))
            paths = [a["path"] for a in result["artifacts"]]
            self.assertEqual(paths, sorted(paths))


class TestEmptyManifest(unittest.TestCase):
    """Empty artifact list is valid but produces zero results."""

    def test_empty_artifacts_list(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = _run_audit(tmp, _manifest([]))
            self.assertTrue(result["manifest_valid"])
            self.assertEqual(result["artifacts"], [])
            self.assertEqual(result["totals"]["total"], 0)


class TestMixedOutcomes(unittest.TestCase):
    """Multiple artifacts with mixed verification outcomes."""

    def test_mixed_verified_missing_mismatch_observed(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            good_content = b"good"
            good_h = _write(tmp / "good.json", good_content)
            bad_content = b"actual"
            _write(tmp / "bad.json", bad_content)
            _write(tmp / "noexpect.json", b"whatever")
            wrong_h = _sha256(b"expected but wrong")

            result = _run_audit(tmp, _manifest([
                {"path": "good.json", "type": "result_record", "expected_sha256": good_h},
                {"path": "bad.json", "type": "result_record", "expected_sha256": wrong_h},
                {"path": "missing.json", "type": "trajectory", "expected_sha256": "c" * 64},
                {"path": "noexpect.json", "type": "campaign_manifest"},
            ]))

            statuses = {a["path"]: a["status"] for a in result["artifacts"]}
            self.assertEqual(statuses["good.json"], "VERIFIED")
            self.assertEqual(statuses["bad.json"], "MISMATCH")
            self.assertEqual(statuses["missing.json"], "MISSING")
            self.assertEqual(statuses["noexpect.json"], "OBSERVED_ONLY")

            t = result["totals"]
            self.assertEqual(t["VERIFIED"], 1)
            self.assertEqual(t["MISMATCH"], 1)
            self.assertEqual(t["MISSING"], 1)
            self.assertEqual(t["OBSERVED_ONLY"], 1)
            self.assertEqual(t["total"], 4)


class TestNeverVerifiedFromOwnHash(unittest.TestCase):
    """The utility must not auto-verify by computing and comparing its own hash."""

    def test_no_expected_hash_means_not_verified(self):
        """An artifact with no expected_sha256 must never be VERIFIED."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write(tmp / "auto.txt", b"some data")
            result = _run_audit(tmp, _manifest([{
                "path": "auto.txt",
                "type": "model_artifact",
            }]))
            art = result["artifacts"][0]
            self.assertNotEqual(art["status"], "VERIFIED",
                "Artifact must not be VERIFIED without an independently provided expected hash")
            self.assertEqual(art["status"], "OBSERVED_ONLY")


class TestReproducibilityPrerequisites(unittest.TestCase):
    """Reproducibility prerequisites section is present and populated."""

    def test_reproducibility_keys_present(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = _run_audit(tmp, _manifest([]))
            prereqs = result.get("reproducibility_prerequisites", {})
            expected_keys = {
                "model_artifact_hash",
                "source_checkpoint_revision",
                "quantization_info",
                "backend_executable_identity",
                "chat_template_identity",
                "context_and_decoding_config",
                "task_fixture_identities",
                "expected_outcomes_and_oracles",
                "raw_trajectories",
                "result_and_latency_records",
            }
            self.assertEqual(set(prereqs.keys()), expected_keys)

    def test_model_artifact_hash_unknown_without_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = _run_audit(tmp, _manifest([]))
            self.assertEqual(
                result["reproducibility_prerequisites"]["model_artifact_hash"],
                "UNKNOWN",
            )

    def test_model_artifact_hash_observed_when_type_present(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write(tmp / "model.gguf.txt", b"placeholder")
            result = _run_audit(tmp, _manifest([{
                "path": "model.gguf.txt",
                "type": "model_artifact",
            }]))
            val = result["reproducibility_prerequisites"]["model_artifact_hash"]
            self.assertNotEqual(val, "UNKNOWN",
                "model_artifact_hash should not be UNKNOWN when a model_artifact is present")


class TestOutputStructure(unittest.TestCase):
    """Output JSON has required top-level fields."""

    def test_required_top_level_fields(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = _run_audit(tmp, _manifest([]))
            for key in ("schema_version", "audited_at", "campaign_id", "root",
                        "manifest_path", "totals", "artifacts",
                        "reproducibility_prerequisites", "reproducibility_details",
                        "errors", "manifest_valid"):
                self.assertIn(key, result, f"Missing top-level key: {key}")

    def test_schema_version_is_one(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            result = _run_audit(tmp, _manifest([]))
            self.assertEqual(result["schema_version"], 1)


# ---------------------------------------------------------------------------
# Finding 1: Output Destination Overwrite & TOCTOU Protection
# ---------------------------------------------------------------------------

class TestFinding1OutputProtection(unittest.TestCase):
    """Regression tests for Finding 1 (P1): Output publication remains vulnerable to TOCTOU."""

    def test_output_targeting_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            content = json.dumps(_manifest([])).encode()
            mpath.write_bytes(content)

            code = auditor.main([str(mpath), str(tmp), "--output", str(mpath)])
            self.assertEqual(code, 2)
            self.assertEqual(mpath.read_bytes(), content, "Manifest was modified by output destination")

    def test_summary_targeting_audited_artifact_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            art_file = tmp / "evidence.dat"
            original_bytes = b"precious_historical_evidence"
            art_file.write_bytes(original_bytes)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([{"path": "evidence.dat", "type": "result_record"}])))

            code = auditor.main([str(mpath), str(tmp), "--summary", str(art_file)])
            self.assertEqual(code, 2)
            self.assertEqual(art_file.read_bytes(), original_bytes, "Audited artifact was overwritten")

    def test_output_and_summary_same_destination_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            dest = tmp / "collision.txt"

            code = auditor.main([str(mpath), str(tmp), "--output", str(dest), "--summary", str(dest)])
            self.assertEqual(code, 2)
            self.assertFalse(dest.exists(), "File should not be created on destination collision")

    def test_existing_symlink_alias_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            original = json.dumps(_manifest([])).encode()
            mpath.write_bytes(original)
            link_out = tmp / "link_manifest.json"
            link_out.symlink_to(mpath)

            code = auditor.main([str(mpath), str(tmp), "--output", str(link_out)])
            self.assertEqual(code, 2)
            self.assertEqual(mpath.read_bytes(), original)

    def test_existing_hardlink_alias_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            art_file = tmp / "original.txt"
            original_bytes = b"immutable_artifact"
            art_file.write_bytes(original_bytes)
            hardlink_file = tmp / "hardlink.txt"
            os.link(art_file, hardlink_file)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([{"path": "original.txt", "type": "result_record"}])))

            code = auditor.main([str(mpath), str(tmp), "--summary", str(hardlink_file)])
            self.assertEqual(code, 2)
            self.assertEqual(art_file.read_bytes(), original_bytes)

    def test_injected_destination_replacement_after_preflight_blocks_overwrite(self):
        """Adversarial TOCTOU test: destination is substituted with symlink after preflight."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            art_file = tmp / "protected.dat"
            original_bytes = b"IMMUTABLE_HISTORICAL_DATA"
            art_file.write_bytes(original_bytes)

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([{"path": "protected.dat", "type": "result_record"}])))

            target_out = tmp / "safe_destination.json"

            # Inject symlink substitution after preflight succeeds
            orig_validate = auditor._validate_output_destinations

            def injected_validation(*args, **kwargs):
                errs = orig_validate(*args, **kwargs)
                # Inject malicious symlink pointing to protected artifact after preflight check
                if target_out.exists():
                    target_out.unlink()
                target_out.symlink_to(art_file)
                return errs

            auditor._validate_output_destinations = injected_validation
            try:
                code = auditor.main([str(mpath), str(tmp), "--output", str(target_out)])
                self.assertEqual(code, 2, "CLI should reject publication when destination points to protected artifact")
                self.assertEqual(art_file.read_bytes(), original_bytes, "Protected artifact was overwritten!")
            finally:
                auditor._validate_output_destinations = orig_validate

    def test_nested_output_directory_symlink_replacement_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))

            outside = tmp / "outside"
            outside.mkdir()
            sub = tmp / "nested_dir"
            sub.symlink_to(outside)

            code = auditor.main([str(mpath), str(tmp), "--output", str(sub / "report.json")])
            self.assertEqual(code, 2)
            self.assertFalse((outside / "report.json").exists())

    def test_failed_publication_preserves_existing_protected_input_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            art = tmp / "artifact.dat"
            sentinel = b"SENTINEL_BYTES_PRESERVED"
            art.write_bytes(sentinel)
            mpath = tmp / "manifest.json"
            m_bytes = json.dumps(_manifest([{"path": "artifact.dat", "type": "result_record"}])).encode()
            mpath.write_bytes(m_bytes)

            code = auditor.main([str(mpath), str(tmp), "--output", str(art)])
            self.assertEqual(code, 2)
            self.assertEqual(art.read_bytes(), sentinel)
            self.assertEqual(mpath.read_bytes(), m_bytes)

    def test_temporary_files_are_cleaned_up_after_failure(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            art = tmp / "artifact.dat"
            art.write_bytes(b"data")
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([{"path": "artifact.dat", "type": "result_record"}])))

            out_dir = tmp / "out_dir"
            out_dir.mkdir()
            out_file = out_dir / "target.json"
            out_file.symlink_to(art)

            auditor.main([str(mpath), str(tmp), "--output", str(out_file)])
            # Verify no .tmp_* files remain in out_dir
            leftover_tmps = [p for p in out_dir.iterdir() if p.name.startswith(".tmp_")]
            self.assertEqual(leftover_tmps, [], "Temporary files leaked in output directory")

    def test_valid_distinct_json_output_destination_works(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            out = tmp / "out.json"

            code = auditor.main([str(mpath), str(tmp), "--output", str(out)])
            self.assertEqual(code, 0)
            self.assertTrue(out.is_file())
            parsed = json.loads(out.read_text())
            self.assertTrue(parsed["manifest_valid"])

    def test_valid_distinct_markdown_summary_destination_works(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            summ = tmp / "summary.md"

            code = auditor.main([str(mpath), str(tmp), "--summary", str(summ)])
            self.assertEqual(code, 0)
            self.assertTrue(summ.is_file())
            self.assertIn("Benchmark Evidence Audit Summary", summ.read_text())

    def test_repeated_successful_publication_produces_complete_valid_files(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            out = tmp / "out.json"

            code1 = auditor.main([str(mpath), str(tmp), "--output", str(out), "--deterministic"])
            bytes1 = out.read_bytes()
            code2 = auditor.main([str(mpath), str(tmp), "--output", str(out), "--deterministic"])
            bytes2 = out.read_bytes()

            self.assertEqual(code1, 0)
            self.assertEqual(code2, 0)
            self.assertEqual(bytes1, bytes2)


# ---------------------------------------------------------------------------
# Finding 2: Conservative Prerequisite Completeness Model
# ---------------------------------------------------------------------------

class TestFinding2PrerequisiteCompleteness(unittest.TestCase):
    """Mandatory regressions for Finding 2 (P2): Campaign prerequisite completeness."""

    def test_1_one_verified_trajectory_without_completeness_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h = _write(tmp / "t1.json", b"single_trajectory")
            res = _run_audit(tmp, _manifest([{"path": "t1.json", "type": "trajectory", "expected_sha256": h}]))

            # Prerequisite completeness is NOT verified when expected-set completeness is unknown
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "PARTIAL")
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertEqual(det["artifact_integrity"], "VERIFIED")
            self.assertEqual(det["expected_set_status"], "UNKNOWN")
            self.assertFalse(det["prerequisite_complete"])

    def test_2_two_verified_trajectories_with_unknown_expected_set_completeness(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h1 = _write(tmp / "t1.json", b"traj1")
            h2 = _write(tmp / "t2.json", b"traj2")
            res = _run_audit(tmp, _manifest([
                {"path": "t1.json", "type": "trajectory", "expected_sha256": h1},
                {"path": "t2.json", "type": "trajectory", "expected_sha256": h2},
            ]))

            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "PARTIAL")
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertEqual(det["artifact_integrity"], "VERIFIED")
            self.assertEqual(det["expected_set_status"], "UNKNOWN")
            self.assertFalse(det["prerequisite_complete"])

    def test_3_complete_independently_established_expected_set_all_verify(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h1 = _write(tmp / "t1.json", b"traj1")
            h2 = _write(tmp / "t2.json", b"traj2")
            trusted_src = "https://specs.example.org/qwen-e00-trajectories.json"
            expected_sets = {
                "raw_trajectories": {
                    "expected_paths": ["t1.json", "t2.json"],
                    "provenance": trusted_src,
                    "independent_oracle": True,
                }
            }
            res = _run_audit(
                tmp,
                _manifest([
                    {"path": "t1.json", "type": "trajectory", "expected_sha256": h1},
                    {"path": "t2.json", "type": "trajectory", "expected_sha256": h2},
                ], expected_sets=expected_sets),
                trusted_sources=[trusted_src],
            )

            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "VERIFIED")
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertEqual(det["artifact_integrity"], "VERIFIED")
            self.assertEqual(det["expected_set_status"], "COMPLETE")
            self.assertTrue(det["prerequisite_complete"])
            self.assertEqual(det["missing_or_unverified"], [])

    def test_4_established_expected_set_with_one_missing_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h1 = _write(tmp / "t1.json", b"traj1")
            trusted_src = "https://specs.example.org/qwen-e00-trajectories.json"
            expected_sets = {
                "raw_trajectories": {
                    "expected_paths": ["t1.json", "t2_missing.json"],
                    "provenance": trusted_src,
                    "independent_oracle": True,
                }
            }
            res = _run_audit(
                tmp,
                _manifest([
                    {"path": "t1.json", "type": "trajectory", "expected_sha256": h1},
                    {"path": "t2_missing.json", "type": "trajectory", "expected_sha256": "0" * 64},
                ], expected_sets=expected_sets),
                trusted_sources=[trusted_src],
            )

            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "INCOMPLETE")
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertFalse(det["prerequisite_complete"])
            self.assertIn("t2_missing.json", det["missing_or_unverified"])

    def test_5_established_expected_set_with_one_mismatch_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h1 = _write(tmp / "t1.json", b"traj1")
            _write(tmp / "t2.json", b"actual_content")
            trusted_src = "https://specs.example.org/qwen-e00-trajectories.json"
            expected_sets = {
                "raw_trajectories": {
                    "expected_paths": ["t1.json", "t2.json"],
                    "provenance": trusted_src,
                    "independent_oracle": True,
                }
            }
            res = _run_audit(
                tmp,
                _manifest([
                    {"path": "t1.json", "type": "trajectory", "expected_sha256": h1},
                    {"path": "t2.json", "type": "trajectory", "expected_sha256": "f" * 64},
                ], expected_sets=expected_sets),
                trusted_sources=[trusted_src],
            )

            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "MISMATCH")
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertFalse(det["prerequisite_complete"])
            self.assertIn("t2.json", det["missing_or_unverified"])

    def test_6_verified_plus_observed_only(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h1 = _write(tmp / "t1.json", b"traj1")
            _write(tmp / "t2.json", b"traj2")
            res = _run_audit(tmp, _manifest([
                {"path": "t1.json", "type": "trajectory", "expected_sha256": h1},
                {"path": "t2.json", "type": "trajectory"},
            ]))
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "PARTIAL")

    def test_7_optional_verified_artifacts_with_missing_mandatory_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h_opt = _write(tmp / "optional.json", b"opt")
            res = _run_audit(tmp, _manifest([
                {"path": "mandatory.json", "type": "trajectory", "expected_sha256": "1" * 64, "mandatory": True},
                {"path": "optional.json", "type": "trajectory", "expected_sha256": h_opt, "mandatory": False},
            ]))
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "INCOMPLETE")

    def test_8_empty_declared_artifact_set(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res = _run_audit(tmp, _manifest([]))
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "UNKNOWN")

    def test_9_incomplete_archive_with_self_declared_completeness_flag(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h1 = _write(tmp / "t1.json", b"traj1")
            expected_sets = {
                "raw_trajectories": {
                    "expected_paths": ["t1.json"],
                    "complete": True,
                    "provenance": "internal-collector-self-claim",
                    "independent_oracle": False,  # Not independently verified!
                }
            }
            res = _run_audit(tmp, _manifest([
                {"path": "t1.json", "type": "trajectory", "expected_sha256": h1},
            ], expected_sets=expected_sets))

            # Unverified self-claim must not be accepted as verified completeness
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "PARTIAL")
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertEqual(det["expected_set_status"], "UNVERIFIED_CLAIM")
            self.assertFalse(det["prerequisite_complete"])

    def test_10_checksum_matched_summary_document_does_not_prove_task_level_oracles(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b'{"task_specifications": "summary_only"}'
            h = _write(tmp / "freeze-manifest.json", content)
            res = _run_audit(tmp, _manifest([
                {"path": "freeze-manifest.json", "type": "oracle_definition", "expected_sha256": h, "is_summary": True},
            ]))

            art = res["artifacts"][0]
            self.assertEqual(art["status"], "VERIFIED", "File checksum integrity verified")

            # Prerequisite completeness is NOT verified: summary does not prove task-level oracles
            self.assertEqual(res["reproducibility_prerequisites"]["expected_outcomes_and_oracles"], "OBSERVED_ONLY")
            det = res["reproducibility_details"]["expected_outcomes_and_oracles"]
            self.assertEqual(det["expected_set_status"], "SUMMARY_ONLY")
            self.assertFalse(det["prerequisite_complete"])
            self.assertIn("summary document does not prove", det["notes"])

    def test_11_checksum_matched_aggregate_report_does_not_prove_complete_latency_records(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b"# Aggregate Benchmark Report\nOverall latency: 120ms\n"
            h = _write(tmp / "campaign-report.md", content)
            res = _run_audit(tmp, _manifest([
                {"path": "campaign-report.md", "type": "result_record", "expected_sha256": h, "is_summary": True},
            ]))

            art = res["artifacts"][0]
            self.assertEqual(art["status"], "VERIFIED", "Report file checksum integrity verified")

            # Prerequisite completeness is NOT verified: aggregate statistics do not prove per-turn latency
            self.assertEqual(res["reproducibility_prerequisites"]["result_and_latency_records"], "OBSERVED_ONLY")
            det = res["reproducibility_details"]["result_and_latency_records"]
            self.assertEqual(det["expected_set_status"], "AGGREGATE_ONLY")
            self.assertFalse(det["prerequisite_complete"])
            self.assertIn("aggregate statistics do not prove", det["notes"])

    def test_12_stable_output_regardless_of_manifest_ordering(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h1 = _write(tmp / "t1.json", b"traj1")
            _write(tmp / "t2.json", b"traj2")
            e1 = {"path": "t1.json", "type": "trajectory", "expected_sha256": h1}
            e2 = {"path": "t2.json", "type": "trajectory", "expected_sha256": "0" * 64}

            res1 = _run_audit(tmp, _manifest([e1, e2], campaign_id="STABLE"))
            res2 = _run_audit(tmp, _manifest([e2, e1], campaign_id="STABLE"))

            self.assertEqual(
                res1["reproducibility_prerequisites"],
                res2["reproducibility_prerequisites"],
            )
            self.assertEqual(
                res1["reproducibility_details"],
                res2["reproducibility_details"],
            )


# ---------------------------------------------------------------------------
# Finding 3: Symlink TOCTOU and O_NOFOLLOW Enforcement
# ---------------------------------------------------------------------------

class TestFinding3SymlinkTOCTOU(unittest.TestCase):
    """Regression tests for Finding 3 (P2): Symlink time-of-check/time-of-use vulnerability."""

    def test_ordinary_regular_file_inside_root(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b"regular_inside_root"
            h = _write(tmp / "subdir" / "data.txt", content)
            res = _run_audit(tmp, _manifest([{
                "path": "subdir/data.txt",
                "type": "result_record",
                "expected_sha256": h,
            }]))
            art = res["artifacts"][0]
            self.assertEqual(art["status"], "VERIFIED")
            self.assertEqual(art["observed_sha256"], h)

    def test_direct_symlink_escaping_root(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            link = tmp / "escape_passwd"
            link.symlink_to("/etc/passwd")
            res = _run_audit(tmp, _manifest([{"path": "escape_passwd", "type": "result_record"}]))
            art = res["artifacts"][0]
            self.assertEqual(art["status"], "UNVERIFIABLE")
            self.assertIsNone(art["observed_sha256"])

    def test_nested_symlink_escaping_root(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sub = tmp / "a" / "b"
            sub.mkdir(parents=True)
            link = sub / "nested_escape"
            link.symlink_to("/etc/passwd")
            res = _run_audit(tmp, _manifest([{"path": "a/b/nested_escape", "type": "result_record"}]))
            art = res["artifacts"][0]
            self.assertEqual(art["status"], "UNVERIFIABLE")
            self.assertIsNone(art["observed_sha256"])

    def test_symlink_directory_component_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            outside = Path(tempfile.mkdtemp())
            try:
                (outside / "secret.txt").write_bytes(b"secret")
                (tmp / "linkdir").symlink_to(outside)
                res = _run_audit(tmp, _manifest([{"path": "linkdir/secret.txt", "type": "result_record"}]))
                art = res["artifacts"][0]
                self.assertEqual(art["status"], "UNVERIFIABLE")
                self.assertIsNone(art["observed_sha256"])
            finally:
                import shutil
                shutil.rmtree(outside)

    def test_safe_handling_of_missing_or_replaced_files(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res = _run_audit(tmp, _manifest([{"path": "missing/dir/file.txt", "type": "result_record"}]))
            art = res["artifacts"][0]
            self.assertEqual(art["status"], "MISSING")

    def test_no_outside_root_file_access(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            secret_outside = Path(tempfile.mkdtemp())
            try:
                secret_file = secret_outside / "secret.dat"
                secret_file.write_bytes(b"TOP_SECRET_DATA")
                secret_hash = _sha256(b"TOP_SECRET_DATA")

                link = tmp / "secret_link"
                link.symlink_to(secret_file)

                res = _run_audit(tmp, _manifest([{
                    "path": "secret_link",
                    "type": "result_record",
                    "expected_sha256": secret_hash,
                }]))
                art = res["artifacts"][0]
                self.assertEqual(art["status"], "UNVERIFIABLE")
                self.assertIsNone(art["observed_sha256"], "Outside-root content was read or hashed")
            finally:
                import shutil
                shutil.rmtree(secret_outside)

    def test_no_descriptor_leaks_after_failure(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            (tmp / "bad_link").symlink_to("/etc/passwd")

            fd_dir = Path(f"/proc/{os.getpid()}/fd")
            if not fd_dir.exists():
                self.skipTest("/proc/self/fd not accessible on this platform")

            # Warm-up run
            _run_audit(tmp, _manifest([{"path": "bad_link", "type": "result_record"}]))

            fds_before = len(list(fd_dir.iterdir()))
            for _ in range(10):
                _run_audit(tmp, _manifest([
                    {"path": "bad_link", "type": "result_record"},
                    {"path": "missing.txt", "type": "result_record"},
                    {"path": "../outside.txt", "type": "result_record"},
                ]))
            fds_after = len(list(fd_dir.iterdir()))
            self.assertEqual(fds_before, fds_after, "File descriptor leak detected")


# ---------------------------------------------------------------------------
# Finding 4: Malformed Manifest Handling
# ---------------------------------------------------------------------------

class TestFinding4MalformedManifest(unittest.TestCase):
    """Regression tests for Finding 4 (P2): Malformed manifest handling."""

    def test_invalid_list_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res = _run_audit(tmp, _manifest([{"path": ["invalid", "list"], "type": "result_record"}]))
            self.assertEqual(res["totals"]["INVALID"], 1)
            self.assertEqual(res["artifacts"][0]["status"], "INVALID")

    def test_invalid_integer_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res = _run_audit(tmp, _manifest([{"path": 12345, "type": "result_record"}]))
            self.assertEqual(res["totals"]["INVALID"], 1)
            self.assertEqual(res["artifacts"][0]["status"], "INVALID")

    def test_invalid_null_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res = _run_audit(tmp, _manifest([{"path": None, "type": "result_record"}]))
            self.assertEqual(res["totals"]["INVALID"], 1)
            self.assertEqual(res["artifacts"][0]["status"], "INVALID")

    def test_invalid_boolean_expected_hash(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write(tmp / "file.txt", b"data")
            res_false = _run_audit(tmp, _manifest([{"path": "file.txt", "type": "result_record", "expected_sha256": False}]))
            self.assertEqual(res_false["totals"]["INVALID"], 1)
            self.assertEqual(res_false["artifacts"][0]["status"], "INVALID")

            res_true = _run_audit(tmp, _manifest([{"path": "file.txt", "type": "result_record", "expected_sha256": True}]))
            self.assertEqual(res_true["totals"]["INVALID"], 1)
            self.assertEqual(res_true["artifacts"][0]["status"], "INVALID")

    def test_invalid_numeric_expected_hash(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write(tmp / "file.txt", b"data")
            res = _run_audit(tmp, _manifest([{"path": "file.txt", "type": "result_record", "expected_sha256": 1234567890}]))
            self.assertEqual(res["totals"]["INVALID"], 1)
            self.assertEqual(res["artifacts"][0]["status"], "INVALID")

    def test_invalid_malformed_hexadecimal_hash(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write(tmp / "file.txt", b"data")
            res = _run_audit(tmp, _manifest([{"path": "file.txt", "type": "result_record", "expected_sha256": "g" * 64}]))
            self.assertEqual(res["totals"]["INVALID"], 1)
            self.assertEqual(res["artifacts"][0]["status"], "INVALID")

    def test_invalid_wrong_length_hash(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            _write(tmp / "file.txt", b"data")
            res = _run_audit(tmp, _manifest([{"path": "file.txt", "type": "result_record", "expected_sha256": "a" * 32}]))
            self.assertEqual(res["totals"]["INVALID"], 1)
            self.assertEqual(res["artifacts"][0]["status"], "INVALID")

    def test_invalid_artifact_record_structure(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res = _run_audit(tmp, _manifest(["not_a_dict", 42, None]))
            self.assertEqual(res["totals"]["INVALID"], 3)
            for art in res["artifacts"]:
                self.assertEqual(art["status"], "INVALID")

    def test_missing_required_path_field(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res = _run_audit(tmp, _manifest([{"type": "result_record"}]))
            self.assertEqual(res["totals"]["INVALID"], 1)
            self.assertEqual(res["artifacts"][0]["status"], "INVALID")

    def test_mixed_valid_and_invalid_entries(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h = _write(tmp / "good.txt", b"content")
            res = _run_audit(tmp, _manifest([
                {"path": "good.txt", "type": "result_record", "expected_sha256": h},
                {"path": ["bad", "list"], "type": "result_record"},
                {"path": "bad_hash.txt", "type": "result_record", "expected_sha256": False},
            ]))
            self.assertEqual(res["totals"]["VERIFIED"], 1)
            self.assertEqual(res["totals"]["INVALID"], 2)


# ---------------------------------------------------------------------------
# Deterministic Output Mode
# ---------------------------------------------------------------------------

class TestDeterministicOutputMode(unittest.TestCase):
    """Test deterministic CLI mode generates identical output bytes."""

    def test_deterministic_cli_flag_produces_identical_bytes(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            out1 = tmp / "out1.json"
            out2 = tmp / "out2.json"

            code1 = auditor.main([str(mpath), str(tmp), "--output", str(out1), "--deterministic"])
            code2 = auditor.main([str(mpath), str(tmp), "--output", str(out2), "--deterministic"])

            self.assertEqual(code1, 0)
            self.assertEqual(code2, 0)
            self.assertEqual(out1.read_bytes(), out2.read_bytes(), "Deterministic runs differed in output bytes")


# ---------------------------------------------------------------------------
# Additional Regressions for Final Security & Evidence Findings
# ---------------------------------------------------------------------------

class TestFinding1ParentDirectorySubstitution(unittest.TestCase):
    """P1: Output parent directory substitution protection."""

    def test_intermediate_directory_symlink_substitution_blocked_and_target_preserved(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # Create a protected target directory containing sensitive files
            protected_dir = tmp / "protected"
            protected_dir.mkdir()
            protected_file = protected_dir / "secret_evidence.dat"
            original_bytes = b"ORIGINAL PROTECTED EVIDENCE - DO NOT OVERWRITE"
            protected_file.write_bytes(original_bytes)

            # Create an intermediate directory structure
            base_out = tmp / "output_root"
            base_out.mkdir()
            # Replace intermediate component with a symlink pointing to the protected directory
            inter_symlink = base_out / "intermediate"
            os.symlink(str(protected_dir), str(inter_symlink))

            # Attempt publication targeting a path through the intermediate symlink
            dest = inter_symlink / "secret_evidence.dat"
            protected_paths, protected_inodes = auditor._collect_protected_targets(
                str(tmp / "manifest.json"),
                str(tmp),
                [{"path": "secret_evidence.dat", "type": "result_record"}],
            )
            # Explicitly register protected file
            protected_paths[protected_file.resolve()] = "protected file"
            st = protected_file.stat()
            protected_inodes[(st.st_dev, st.st_ino)] = "protected file"

            with self.assertRaises(OSError) as ctx:
                auditor._publish_file_securely(
                    str(dest),
                    "MALICIOUS OVERWRITE CONTENT",
                    protected_paths,
                    protected_inodes,
                )

            self.assertIn("symlink", str(ctx.exception).lower())
            # Verify the protected file bytes are completely unchanged
            self.assertEqual(protected_file.read_bytes(), original_bytes)

    def test_cli_intermediate_directory_symlink_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))

            outside = tmp / "outside"
            outside.mkdir()
            canary = outside / "canary.txt"
            canary.write_bytes(b"CANARY UNTOUCHED")

            out_tree = tmp / "out_tree"
            out_tree.mkdir()
            sym_dir = out_tree / "sym_sub"
            os.symlink(str(outside), str(sym_dir))

            target_out = sym_dir / "out.json"
            code = auditor.main([str(mpath), str(tmp), "--output", str(target_out)])
            self.assertEqual(code, 2)
            self.assertEqual(canary.read_bytes(), b"CANARY UNTOUCHED")


class TestFinding2ArtifactRootSubstitution(unittest.TestCase):
    """P2: Artifact root substitution and outside-root read protection."""

    def test_root_directory_symlink_rejected_and_outside_file_not_verified(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            # Create outside secret directory
            outside_dir = tmp / "outside_secret"
            outside_dir.mkdir()
            secret_file = outside_dir / "confidential.dat"
            secret_bytes = b"CONFIDENTIAL SYSTEM DATA"
            secret_file.write_bytes(secret_bytes)
            expected_hash = _sha256(secret_bytes)

            # Create symlink root pointing to outside directory
            symlink_root = tmp / "symlink_root"
            os.symlink(str(outside_dir), str(symlink_root))

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([{
                "path": "confidential.dat",
                "type": "result_record",
                "expected_sha256": expected_hash,
            }])))

            # Audit against symlink root
            res = auditor.audit(str(mpath), str(symlink_root))

            art = res["artifacts"][0]
            self.assertEqual(art["status"], "UNVERIFIABLE")
            self.assertFalse(art["present"])
            self.assertIsNone(art["observed_sha256"])
            self.assertFalse(art["hash_match"])
            self.assertIn("root directory is invalid or a symlink", art["notes"])
            self.assertEqual(res["totals"]["VERIFIED"], 0)
            self.assertEqual(res["totals"]["UNVERIFIABLE"], 1)

    def test_root_replaced_with_symlink_during_audit(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            real_root = tmp / "root_dir"
            real_root.mkdir()
            outside = tmp / "outside"
            outside.mkdir()
            h = _write(outside / "data.txt", b"outside data")

            # Replace real root with symlink
            real_root.rmdir()
            os.symlink(str(outside), str(real_root))

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([{
                "path": "data.txt",
                "type": "result_record",
                "expected_sha256": h,
            }])))

            res = auditor.audit(str(mpath), str(real_root))
            self.assertEqual(res["artifacts"][0]["status"], "UNVERIFIABLE")
            self.assertEqual(res["totals"]["VERIFIED"], 0)

    def test_no_descriptor_leaks_after_root_symlink_rejections(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sym_root = tmp / "sym_root"
            os.symlink(str(tmp), str(sym_root))
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([{"path": "file.txt", "type": "result_record"}])))

            fd_dir = Path(f"/proc/{os.getpid()}/fd")
            if not fd_dir.exists():
                self.skipTest("/proc/self/fd not accessible on this platform")

            # Warm-up run
            auditor.audit(str(mpath), str(sym_root))
            fds_before = len(list(fd_dir.iterdir()))

            for _ in range(10):
                auditor.audit(str(mpath), str(sym_root))

            fds_after = len(list(fd_dir.iterdir()))
            self.assertEqual(fds_before, fds_after, "Descriptor leak detected after root symlink rejections")


class TestFinding3ProvenanceTrust(unittest.TestCase):
    """P2: Self-attested independent provenance and trusted verification source."""

    def test_fabricated_provenance_url_cannot_produce_verified(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h1 = _write(tmp / "t1.json", b"traj1")
            h2 = _write(tmp / "t2.json", b"traj2")
            expected_sets = {
                "raw_trajectories": {
                    "expected_paths": ["t1.json", "t2.json"],
                    "provenance": "https://fabricated-external-oracle.org/claims.json",
                    "independent_oracle": True,
                }
            }
            # Run without trusted source
            res = _run_audit(tmp, _manifest([
                {"path": "t1.json", "type": "trajectory", "expected_sha256": h1},
                {"path": "t2.json", "type": "trajectory", "expected_sha256": h2},
            ], expected_sets=expected_sets))

            # Must NOT produce VERIFIED
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "PARTIAL")
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertEqual(det["artifact_integrity"], "VERIFIED")
            self.assertEqual(det["expected_set_status"], "UNVERIFIED")
            self.assertEqual(det["expected_set_source"], "declared")
            self.assertFalse(det["prerequisite_complete"])
            self.assertIn("lacks separately trusted verification source", det["notes"])

    def test_self_declared_completeness_flag_cannot_produce_verified(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h1 = _write(tmp / "t1.json", b"traj1")
            expected_sets = {
                "raw_trajectories": {
                    "expected_paths": ["t1.json"],
                    "complete": True,
                    "provenance": "internal-self-assertion",
                }
            }
            res = _run_audit(tmp, _manifest([
                {"path": "t1.json", "type": "trajectory", "expected_sha256": h1},
            ], expected_sets=expected_sets))

            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "PARTIAL")
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertEqual(det["expected_set_status"], "UNVERIFIED_CLAIM")
            self.assertFalse(det["prerequisite_complete"])

    def test_partial_manifest_with_trusted_source_cannot_produce_verified(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h1 = _write(tmp / "t1.json", b"traj1")
            trusted_src = "https://official-specs.org/e00.json"
            expected_sets = {
                "raw_trajectories": {
                    "expected_paths": ["t1.json", "t2_missing.json"],
                    "provenance": trusted_src,
                    "independent_oracle": True,
                }
            }
            res = _run_audit(
                tmp,
                _manifest([
                    {"path": "t1.json", "type": "trajectory", "expected_sha256": h1},
                ], expected_sets=expected_sets),
                trusted_sources=[trusted_src],
            )

            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "INCOMPLETE")
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertFalse(det["prerequisite_complete"])
            self.assertIn("t2_missing.json", det["missing_or_unverified"])

    def test_untrusted_provenance_mismatch_cannot_produce_verified(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h1 = _write(tmp / "t1.json", b"traj1")
            expected_sets = {
                "raw_trajectories": {
                    "expected_paths": ["t1.json"],
                    "provenance": "https://unapproved-oracle.org",
                    "independent_oracle": True,
                }
            }
            res = _run_audit(
                tmp,
                _manifest([
                    {"path": "t1.json", "type": "trajectory", "expected_sha256": h1},
                ], expected_sets=expected_sets),
                trusted_sources=["https://approved-oracle.org"],
            )

            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "PARTIAL")
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertEqual(det["expected_set_status"], "UNVERIFIED")
            self.assertFalse(det["prerequisite_complete"])

    def test_external_trusted_expected_sets_file_works(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h1 = _write(tmp / "t1.json", b"traj1")
            h2 = _write(tmp / "t2.json", b"traj2")
            trusted_sets_file = tmp / "trusted_sets.json"
            trusted_sets_file.write_text(json.dumps({
                "raw_trajectories": {
                    "expected_paths": ["t1.json", "t2.json"],
                    "provenance": "official-e00-spec",
                }
            }))
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": "t1.json", "type": "trajectory", "expected_sha256": h1},
                {"path": "t2.json", "type": "trajectory", "expected_sha256": h2},
            ])))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(trusted_sets_file))
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "VERIFIED")
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertEqual(det["expected_set_status"], "COMPLETE")
            self.assertEqual(det["expected_set_source"], "externally_verified")
            self.assertTrue(det["prerequisite_complete"])


class TestFinding4MalformedExpectedSets(unittest.TestCase):
    """P2: Malformed expected-set definitions and structural validation."""

    def test_expected_sets_as_list_handled_without_attribute_error(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mdata = _manifest([])
            mdata["expected_sets"] = []  # list instead of dict
            mpath.write_text(json.dumps(mdata))

            res = auditor.audit(str(mpath), str(tmp))
            self.assertFalse(res["manifest_valid"])
            self.assertTrue(any("expected_sets must be a JSON object" in e for e in res["errors"]))

            # Direct call to _build_prereqs with list must also not raise AttributeError
            prereqs, details = auditor._build_prereqs([], [])
            self.assertEqual(prereqs["raw_trajectories"], "INVALID")
            self.assertEqual(details["raw_trajectories"]["status"], "INVALID")

    def test_expected_paths_containing_dict_object_handled_without_type_error(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mdata = _manifest([])
            mdata["expected_sets"] = {
                "raw_trajectories": {
                    "expected_paths": [{"nested": "unhashable_dict"}],
                }
            }
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(mdata))

            res = auditor.audit(str(mpath), str(tmp))
            self.assertFalse(res["manifest_valid"])
            self.assertTrue(any("must be string" in e for e in res["errors"]))

            # Direct call to _build_prereqs must also handle safely
            prereqs, details = auditor._build_prereqs([], mdata["expected_sets"])
            self.assertEqual(prereqs["raw_trajectories"], "INVALID")
            self.assertEqual(details["raw_trajectories"]["expected_set_status"], "INVALID")

    def test_empty_expected_paths_list_does_not_produce_verified(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            h = _write(tmp / "t1.json", b"content")
            trusted_src = "https://specs.example.org/oracle.json"
            mdata = _manifest([
                {"path": "t1.json", "type": "trajectory", "expected_sha256": h},
            ])
            mdata["expected_sets"] = {
                "raw_trajectories": {
                    "expected_paths": [],  # Empty list!
                    "provenance": trusted_src,
                    "independent_oracle": True,
                }
            }
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(mdata))

            res = auditor.audit(str(mpath), str(tmp), trusted_sources=[trusted_src])
            self.assertNotEqual(res["reproducibility_prerequisites"]["raw_trajectories"], "VERIFIED")
            self.assertFalse(res["manifest_valid"])
            self.assertTrue(any("cannot be empty" in e for e in res["errors"]))

            # Direct call to _build_prereqs must also handle safely without producing VERIFIED
            prereqs, details = auditor._build_prereqs(
                [{"path": "t1.json", "type": "trajectory", "status": "VERIFIED"}],
                mdata["expected_sets"],
                trusted_sources=[trusted_src],
            )
            self.assertNotEqual(prereqs["raw_trajectories"], "VERIFIED")
            self.assertEqual(prereqs["raw_trajectories"], "INVALID")
            self.assertFalse(details["raw_trajectories"]["prerequisite_complete"])

    def test_expected_paths_with_non_string_items_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mdata = _manifest([])
            mdata["expected_sets"] = {
                "raw_trajectories": {
                    "expected_paths": [123, True, None],
                }
            }
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(mdata))

            res = auditor.audit(str(mpath), str(tmp))
            self.assertFalse(res["manifest_valid"])

    def test_expected_paths_with_duplicate_paths_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mdata = _manifest([])
            mdata["expected_sets"] = {
                "raw_trajectories": {
                    "expected_paths": ["t1.json", "t1.json"],
                }
            }
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(mdata))

            res = auditor.audit(str(mpath), str(tmp))
            self.assertFalse(res["manifest_valid"])
            self.assertTrue(any("duplicate path" in e for e in res["errors"]))

    def test_expected_count_invalid_type_or_negative_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mdata = _manifest([])
            mdata["expected_sets"] = {
                "raw_trajectories": {
                    "expected_count": -5,
                }
            }
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(mdata))

            res = auditor.audit(str(mpath), str(tmp))
            self.assertFalse(res["manifest_valid"])
            self.assertTrue(any("positive integer" in e for e in res["errors"]))


# ---------------------------------------------------------------------------
# Finding 1: P1, Trusted Input Overwrite Protection
# ---------------------------------------------------------------------------

class TestFinding1TrustedInputProtection(unittest.TestCase):
    """Output publication must never overwrite any trusted input file."""

    def test_output_targeting_trusted_expected_sets_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted_sets.json"
            tbytes = json.dumps({"raw_trajectories": {"expected_count": 1}}).encode()
            tpath.write_bytes(tbytes)

            code = auditor.main([
                str(mpath), str(tmp),
                "--trusted-expected-sets", str(tpath),
                "--output", str(tpath),
            ])
            self.assertEqual(code, 2)
            self.assertEqual(tpath.read_bytes(), tbytes, "Trusted expected sets file was overwritten")

    def test_summary_targeting_trusted_expected_sets_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted_sets.json"
            tbytes = json.dumps({"raw_trajectories": {"expected_count": 1}}).encode()
            tpath.write_bytes(tbytes)

            code = auditor.main([
                str(mpath), str(tmp),
                "--trusted-expected-sets", str(tpath),
                "--summary", str(tpath),
            ])
            self.assertEqual(code, 2)
            self.assertEqual(tpath.read_bytes(), tbytes, "Trusted expected sets file was overwritten by summary")

    def test_output_symlink_alias_targeting_trusted_expected_sets_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted_sets.json"
            tbytes = json.dumps({"raw_trajectories": {"expected_count": 1}}).encode()
            tpath.write_bytes(tbytes)
            link_out = tmp / "link_trusted.json"
            link_out.symlink_to(tpath)

            code = auditor.main([
                str(mpath), str(tmp),
                "--trusted-expected-sets", str(tpath),
                "--output", str(link_out),
            ])
            self.assertEqual(code, 2)
            self.assertEqual(tpath.read_bytes(), tbytes, "Trusted expected sets overwritten via symlink alias")

    def test_output_hardlink_alias_targeting_trusted_expected_sets_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted_sets.json"
            tbytes = json.dumps({"raw_trajectories": {"expected_count": 1}}).encode()
            tpath.write_bytes(tbytes)
            hlink_out = tmp / "hlink_trusted.json"
            os.link(tpath, hlink_out)

            code = auditor.main([
                str(mpath), str(tmp),
                "--trusted-expected-sets", str(tpath),
                "--output", str(hlink_out),
            ])
            self.assertEqual(code, 2)
            self.assertEqual(tpath.read_bytes(), tbytes, "Trusted expected sets overwritten via hardlink alias")

    def test_distinct_legitimate_destinations_with_trusted_sets_succeeds(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b'{"task": "c5"}'
            chash = hashlib.sha256(content).hexdigest()
            (tmp / "t1.json").write_bytes(content)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([{
                "path": "t1.json",
                "type": "trajectory",
                "expected_sha256": chash,
                "size_bytes": len(content),
            }])))
            tpath = tmp / "trusted_sets.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_paths": ["t1.json"]}}))

            out = tmp / "out.json"
            summ = tmp / "summary.md"
            code = auditor.main([
                str(mpath), str(tmp),
                "--trusted-expected-sets", str(tpath),
                "--output", str(out),
                "--summary", str(summ),
                "--deterministic",
            ])
            self.assertEqual(code, 0)
            self.assertTrue(out.is_file())
            self.assertTrue(summ.is_file())

    def test_adversarial_post_preflight_substitution_targeting_trusted_input_blocked(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            tpath = tmp / "trusted_sets.json"
            original_bytes = b'{"immutable": "evidence"}'
            tpath.write_bytes(original_bytes)

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))

            protected_paths, protected_inodes = auditor._collect_protected_targets(
                str(mpath),
                str(tmp),
                [],
                extra_input_paths=[tpath],
            )
            self.assertIn(tpath.resolve(), protected_paths)
            self.assertIn((tpath.stat().st_dev, tpath.stat().st_ino), protected_inodes)

            out_dest = tmp / "attack_out.json"
            out_dest.symlink_to(tpath)

            with self.assertRaises(OSError):
                auditor._publish_file_securely(
                    str(out_dest),
                    "attacker payload",
                    protected_paths,
                    protected_inodes,
                )
            self.assertEqual(tpath.read_bytes(), original_bytes)


# ---------------------------------------------------------------------------
# Finding 2: P2, Dot-Only Path Crash Rejection
# ---------------------------------------------------------------------------

class TestFinding2DotPathRejection(unittest.TestCase):
    """Dot-only path components must be rejected cleanly as INVALID without IndexError."""

    def test_dot_path_in_manifest_classified_as_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res = _run_audit(tmp, _manifest([{"path": ".", "type": "trajectory"}]))
            self.assertEqual(len(res["artifacts"]), 1)
            art = res["artifacts"][0]
            self.assertEqual(art["status"], auditor.INVALID)
            self.assertEqual(res["totals"][auditor.INVALID], 1)

    def test_dot_slash_path_in_manifest_classified_as_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res = _run_audit(tmp, _manifest([{"path": "./", "type": "trajectory"}]))
            self.assertEqual(len(res["artifacts"]), 1)
            art = res["artifacts"][0]
            self.assertEqual(art["status"], auditor.INVALID)

    def test_dot_slash_dot_path_classified_as_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res = _run_audit(tmp, _manifest([{"path": "././", "type": "trajectory"}]))
            self.assertEqual(len(res["artifacts"]), 1)
            self.assertEqual(res["artifacts"][0]["status"], auditor.INVALID)

    def test_multi_dot_slash_path_classified_as_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res = _run_audit(tmp, _manifest([{"path": ".///.", "type": "trajectory"}]))
            self.assertEqual(len(res["artifacts"]), 1)
            self.assertEqual(res["artifacts"][0]["status"], auditor.INVALID)

    def test_empty_path_classified_as_invalid(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            res = _run_audit(tmp, _manifest([{"path": "", "type": "trajectory"}]))
            self.assertEqual(len(res["artifacts"]), 1)
            self.assertEqual(res["artifacts"][0]["status"], auditor.INVALID)

    def test_valid_nested_path_accepted(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sub = tmp / "nested"
            sub.mkdir()
            content = b"trajectory_content"
            (sub / "traj.json").write_bytes(content)
            chash = hashlib.sha256(content).hexdigest()
            res = _run_audit(tmp, _manifest([{
                "path": "nested/traj.json",
                "type": "trajectory",
                "expected_sha256": chash,
                "size_bytes": len(content),
            }]))
            self.assertEqual(len(res["artifacts"]), 1)
            self.assertEqual(res["artifacts"][0]["status"], auditor.VERIFIED)

    def test_mixed_manifest_with_dot_path_and_valid_path(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b"valid_data"
            (tmp / "valid.json").write_bytes(content)
            chash = hashlib.sha256(content).hexdigest()

            res = _run_audit(tmp, _manifest([
                {"path": ".", "type": "trajectory"},
                {"path": "valid.json", "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
            ]))
            self.assertEqual(res["totals"][auditor.INVALID], 1)
            self.assertEqual(res["totals"][auditor.VERIFIED], 1)
            by_path = {a["path"]: a["status"] for a in res["artifacts"]}
            self.assertEqual(by_path["."], auditor.INVALID)
            self.assertEqual(by_path["valid.json"], auditor.VERIFIED)

    def test_open_and_hash_artifact_direct_dot_calls_never_raise_index_error(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for bad_path in [".", "./", "././", ".///.", "", "   "]:
                status, chash, size, note = auditor._open_and_hash_artifact(tmp, bad_path)
                self.assertEqual(status, auditor.INVALID)
                self.assertIsNone(chash)
                self.assertIsNone(size)
                self.assertIsNotNone(note)


# ---------------------------------------------------------------------------
# Finding 3: P2, Unchecked Trusted-Expected-Sets Structure
# ---------------------------------------------------------------------------

class TestFinding3MalformedTrustedExpectedSets(unittest.TestCase):
    """External trusted expected-sets configuration must be strictly validated."""

    def test_trusted_sets_top_level_list_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text("[]")

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("must be a JSON object" in e for e in res["errors"]))
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.INVALID)

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_trusted_sets_top_level_null_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text("null")

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("cannot be null" in e for e in res["errors"]))

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_trusted_sets_top_level_empty_dict_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text("{}")

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("cannot be an empty object" in e for e in res["errors"]))

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_trusted_sets_invalid_json_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text("THIS IS NOT JSON {{{{")

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("cannot load trusted_expected_sets" in e for e in res["errors"]))

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_trusted_sets_unknown_category_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"fabricated_unknown_category": {"expected_count": 1}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("unrecognized or unsupported prerequisite category" in e for e in res["errors"]))

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_trusted_sets_empty_expected_paths_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_paths": []}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("cannot be empty" in e for e in res["errors"]))

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_trusted_sets_non_string_paths_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_paths": [42, False, None]}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("must be string" in e for e in res["errors"]))

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_trusted_sets_duplicate_paths_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_paths": ["a.json", "a.json"]}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("duplicate path" in e for e in res["errors"]))

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_trusted_sets_unsafe_path_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_paths": ["../escape.json"]}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("unsafe or invalid path" in e for e in res["errors"]))

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_trusted_sets_invalid_expected_count_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_count": -3}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("positive integer" in e for e in res["errors"]))

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_trusted_sets_conflicting_paths_and_count_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({
                "raw_trajectories": {
                    "expected_paths": ["a.json", "b.json"],
                    "expected_count": 5,
                }
            }))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("conflicting definitions" in e for e in res["errors"]))

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_trusted_sets_invalid_provenance_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_count": 1, "provenance": "   "}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("invalid provenance" in e for e in res["errors"]))

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_trusted_sets_unsupported_fields_rejected(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))
            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_count": 1, "illegal_field": True}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("unsupported field(s)" in e for e in res["errors"]))

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2)

    def test_valid_trusted_sets_with_complete_evidence_verified(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b"evidence_payload"
            chash = hashlib.sha256(content).hexdigest()
            (tmp / "task1.json").write_bytes(content)

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([{
                "path": "task1.json",
                "type": "trajectory",
                "expected_sha256": chash,
                "size_bytes": len(content),
            }])))

            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_paths": ["task1.json"]}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertTrue(res["configuration_valid"])
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.VERIFIED)

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 0)

    def test_valid_trusted_sets_with_incomplete_evidence_yields_code_1_not_2(self):
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b"partial_data"
            chash = hashlib.sha256(content).hexdigest()
            (tmp / "t1.json").write_bytes(content)

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([{
                "path": "t1.json",
                "type": "trajectory",
                "expected_sha256": chash,
                "size_bytes": len(content),
            }])))

            # Trusted expected sets requires 5 trajectories, but only 1 is present
            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_count": 5}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertTrue(res["configuration_valid"])
            self.assertEqual(len(res["errors"]), 0)
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.INCOMPLETE)

            # Valid configuration with incomplete evidence must return exit code 1, NOT exit code 2
            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 1)


# ---------------------------------------------------------------------------
# P2: Duplicate Evidence and Normalized Path Protection
# ---------------------------------------------------------------------------

class TestP2DuplicateEvidenceProtection(unittest.TestCase):
    """Campaign completeness must never be established by duplicate evidence."""

    def test_1_physical_file_declared_twice_with_exact_same_path_cannot_satisfy_count(self):
        """Case A: One physical file declared twice cannot satisfy an expected count of two."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b'{"trajectory_id": "traj_01"}'
            chash = hashlib.sha256(content).hexdigest()
            (tmp / "t1.json").write_bytes(content)

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": "t1.json", "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
                {"path": "t1.json", "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
            ])))

            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_count": 2}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertFalse(det["prerequisite_complete"])
            self.assertEqual(det["expected_set_status"], "INCOMPLETE")
            self.assertEqual(det["distinct_verified_count"], 1)
            self.assertTrue(len(det["duplicate_artifacts"]) > 0)
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.INCOMPLETE)

            # CLI execution assertion
            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 1, "Duplicate evidence should return exit code 1 (incomplete evidence)")

    def test_2_file_declared_as_plain_and_dot_slash_path_cannot_be_counted_separately(self):
        """Case B: 'artifact.bin' and './artifact.bin' cannot be counted separately."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b"artifact_binary_payload"
            chash = hashlib.sha256(content).hexdigest()
            (tmp / "artifact.bin").write_bytes(content)

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": "artifact.bin", "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
                {"path": "./artifact.bin", "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
            ])))

            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_count": 2}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertFalse(det["prerequisite_complete"])
            self.assertEqual(det["expected_set_status"], "INCOMPLETE")
            self.assertEqual(det["distinct_verified_count"], 1)
            self.assertTrue(any("normalized path alias" in d for d in det["duplicate_artifacts"]))
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.INCOMPLETE)

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 1)

    def test_3_duplicate_nested_relative_paths_with_redundant_dot_slash(self):
        """Duplicate nested relative paths containing redundant dot components are detected."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            sub = tmp / "nested"
            sub.mkdir()
            content = b"nested_trajectory_data"
            chash = hashlib.sha256(content).hexdigest()
            (sub / "traj.json").write_bytes(content)

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": "nested/traj.json", "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
                {"path": "./nested/./traj.json", "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
            ])))

            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_count": 2}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertFalse(det["prerequisite_complete"])
            self.assertEqual(det["distinct_verified_count"], 1)
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.INCOMPLETE)

    def test_4_expected_count_of_two_with_only_one_distinct_verified_artifact(self):
        """Expected count of two with only one distinct artifact reports incomplete."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b"only_one_file"
            chash = hashlib.sha256(content).hexdigest()
            (tmp / "single.json").write_bytes(content)

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": "single.json", "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
            ])))

            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_count": 2}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertFalse(det["prerequisite_complete"])
            self.assertEqual(det["expected_set_status"], "INCOMPLETE")
            self.assertEqual(det["distinct_verified_count"], 1)
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.INCOMPLETE)

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 1)

    def test_5_expected_set_containing_normalized_duplicate_paths_rejected(self):
        """Expected-set definition containing normalized duplicates is rejected as configuration error."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([])))

            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({
                "raw_trajectories": {
                    "expected_paths": ["sub/traj.json", "./sub/./traj.json"],
                }
            }))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            self.assertFalse(res["configuration_valid"])
            self.assertTrue(any("duplicate path in expected_paths" in e for e in res["errors"]))
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.INVALID)

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 2, "Normalized duplicate in expected_paths must exit code 2")

    def test_6_two_genuinely_distinct_checksum_verified_artifacts_satisfy_count(self):
        """Case C: Two genuinely distinct verified artifacts satisfy an expected count of two."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            c1 = b'{"task": "distinct_01"}'
            c2 = b'{"task": "distinct_02"}'
            h1 = hashlib.sha256(c1).hexdigest()
            h2 = hashlib.sha256(c2).hexdigest()
            (tmp / "t1.json").write_bytes(c1)
            (tmp / "t2.json").write_bytes(c2)

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": "t1.json", "type": "trajectory", "expected_sha256": h1, "size_bytes": len(c1)},
                {"path": "t2.json", "type": "trajectory", "expected_sha256": h2, "size_bytes": len(c2)},
            ])))

            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_count": 2}}))

            out = tmp / "out.json"
            code = auditor.main([
                str(mpath), str(tmp),
                "--trusted-expected-sets", str(tpath),
                "--output", str(out),
            ])
            self.assertEqual(code, 0)
            res = json.loads(out.read_text())
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertTrue(det["prerequisite_complete"])
            self.assertEqual(det["expected_set_status"], "COMPLETE")
            self.assertEqual(det["distinct_verified_count"], 2)
            self.assertEqual(det["duplicate_artifacts"], [])
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.VERIFIED)

    def test_7_explicit_expected_paths_where_one_required_artifact_is_absent(self):
        """Explicit expected paths where one required artifact is absent remains incomplete."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            c1 = b"present_payload"
            h1 = hashlib.sha256(c1).hexdigest()
            (tmp / "t1.json").write_bytes(c1)

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": "t1.json", "type": "trajectory", "expected_sha256": h1, "size_bytes": len(c1)},
            ])))

            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({
                "raw_trajectories": {
                    "expected_paths": ["t1.json", "t2.json"],
                }
            }))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertFalse(det["prerequisite_complete"])
            self.assertEqual(det["expected_set_status"], "INCOMPLETE")
            self.assertIn("t2.json", det["missing_or_unverified"])
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.INCOMPLETE)

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 1)

    def test_8_explicit_expected_paths_where_every_required_artifact_verifies(self):
        """Explicit expected paths where every required distinct artifact verifies produces complete."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            c1 = b"payload_01"
            c2 = b"payload_02"
            h1 = hashlib.sha256(c1).hexdigest()
            h2 = hashlib.sha256(c2).hexdigest()
            (tmp / "t1.json").write_bytes(c1)
            (tmp / "t2.json").write_bytes(c2)

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": "./t1.json", "type": "trajectory", "expected_sha256": h1, "size_bytes": len(c1)},
                {"path": "t2.json", "type": "trajectory", "expected_sha256": h2, "size_bytes": len(c2)},
            ])))

            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({
                "raw_trajectories": {
                    "expected_paths": ["t1.json", "t2.json"],
                }
            }))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertTrue(det["prerequisite_complete"])
            self.assertEqual(det["expected_set_status"], "COMPLETE")
            self.assertEqual(det["missing_or_unverified"], [])
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.VERIFIED)

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 0)

    def test_9_manifest_entry_ordering_does_not_change_results(self):
        """Manifest entry ordering produces identical deterministic audit outcomes."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            c1 = b"first_file"
            c2 = b"second_file"
            h1 = hashlib.sha256(c1).hexdigest()
            h2 = hashlib.sha256(c2).hexdigest()
            (tmp / "a1.json").write_bytes(c1)
            (tmp / "b2.json").write_bytes(c2)

            e1 = {"path": "a1.json", "type": "trajectory", "expected_sha256": h1, "size_bytes": len(c1)}
            e2 = {"path": "b2.json", "type": "trajectory", "expected_sha256": h2, "size_bytes": len(c2)}

            mpath = tmp / "manifest.json"
            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_count": 2}}))

            out1 = tmp / "out1.json"
            out2 = tmp / "out2.json"

            mpath.write_text(json.dumps(_manifest([e1, e2])))
            code1 = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath), "--output", str(out1), "--deterministic"])

            mpath.write_text(json.dumps(_manifest([e2, e1])))
            code2 = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath), "--output", str(out2), "--deterministic"])

            self.assertEqual(code1, 0)
            self.assertEqual(code2, 0)
            self.assertEqual(out1.read_bytes(), out2.read_bytes())

    def test_10_duplicate_evidence_never_produces_prerequisite_complete_or_verified(self):
        """Duplicate evidence never produces prerequisite_complete: true or VERIFIED status."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b"sole_evidence_payload"
            chash = hashlib.sha256(content).hexdigest()
            (tmp / "sole.json").write_bytes(content)

            # Even if expected_count is 1, a manifest with duplicate entries must not report prerequisite_complete
            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": "sole.json", "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
                {"path": "sole.json", "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
            ])))

            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_count": 1}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertFalse(det["prerequisite_complete"], "Duplicate evidence must not produce prerequisite_complete: true")
            self.assertNotEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.VERIFIED)
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.INCOMPLETE)

    def test_11_hardlink_alias_detected_and_cannot_satisfy_expected_count(self):
        """Filesystem hardlink alias to the same inode cannot satisfy an expected count of two."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            content = b"hardlink_target_bytes"
            chash = hashlib.sha256(content).hexdigest()
            (tmp / "orig.json").write_bytes(content)
            os.link(tmp / "orig.json", tmp / "hardlink_alias.json")

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": "orig.json", "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
                {"path": "hardlink_alias.json", "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
            ])))

            tpath = tmp / "trusted.json"
            tpath.write_text(json.dumps({"raw_trajectories": {"expected_count": 2}}))

            res = auditor.audit(str(mpath), str(tmp), trusted_expected_sets=str(tpath))
            det = res["reproducibility_details"]["raw_trajectories"]
            self.assertFalse(det["prerequisite_complete"])
            self.assertEqual(det["expected_set_status"], "INCOMPLETE")
            self.assertEqual(det["distinct_verified_count"], 1)
            self.assertTrue(any("hardlinks" in d for d in det["duplicate_artifacts"]))
            self.assertEqual(res["reproducibility_prerequisites"]["raw_trajectories"], auditor.INCOMPLETE)

            code = auditor.main([str(mpath), str(tmp), "--trusted-expected-sets", str(tpath)])
            self.assertEqual(code, 1)


# ---------------------------------------------------------------------------
# P2: Malformed Filename Rejection (Embedded NUL and Unpaired Unicode Surrogates)
# ---------------------------------------------------------------------------

class TestMalformedFilenameRejection(unittest.TestCase):
    """Malformed filesystem filenames must be rejected cleanly without traceback."""

    def test_1_filename_containing_nul_classified_invalid(self):
        """Case 1: Filename containing embedded NUL is classified as INVALID without crash."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            bad_path = "artifact\x00.txt"
            mpath.write_text(json.dumps(_manifest([
                {"path": bad_path, "type": "result_record"}
            ])))
            res = auditor.audit(str(mpath), str(tmp))
            self.assertEqual(res["totals"][auditor.INVALID], 1)
            self.assertEqual(len(res["artifacts"]), 1)
            art = res["artifacts"][0]
            self.assertEqual(art["status"], auditor.INVALID)
            self.assertFalse(art["present"])
            self.assertEqual(art["path"], bad_path)

    def test_2_filename_containing_unpaired_high_surrogate_classified_invalid(self):
        """Case 2: Filename containing unpaired high surrogate U+D800 is classified as INVALID."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            bad_path = f"artifact{chr(0xd800)}.txt"
            mpath.write_text(json.dumps(_manifest([
                {"path": bad_path, "type": "result_record"}
            ])))
            res = auditor.audit(str(mpath), str(tmp))
            self.assertEqual(res["totals"][auditor.INVALID], 1)
            art = res["artifacts"][0]
            self.assertEqual(art["status"], auditor.INVALID)
            self.assertFalse(art["present"])

    def test_3_filename_containing_unpaired_low_surrogate_classified_invalid(self):
        """Case 3: Filename containing unpaired low surrogate (U+DC00 / U+DFFF) is classified as INVALID."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for surrogate_cp in (0xdc00, 0xdc80, 0xdfff):
                mpath = tmp / f"manifest_{surrogate_cp}.json"
                bad_name = f"artifact{chr(surrogate_cp)}.txt"
                mpath.write_text(json.dumps(_manifest([
                    {"path": bad_name, "type": "result_record"}
                ])))
                res = auditor.audit(str(mpath), str(tmp))
                self.assertEqual(res["totals"][auditor.INVALID], 1)
                self.assertEqual(res["artifacts"][0]["status"], auditor.INVALID)

    def test_4_nul_in_nested_relative_path_component(self):
        """Case 4: NUL in nested directory or filename relative path component is classified as INVALID."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            bad_path = "nested/dir\x00component/artifact.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": bad_path, "type": "result_record"}
            ])))
            res = auditor.audit(str(mpath), str(tmp))
            self.assertEqual(res["totals"][auditor.INVALID], 1)
            self.assertEqual(res["artifacts"][0]["status"], auditor.INVALID)

    def test_5_valid_unicode_filename_with_non_ascii_characters(self):
        """Case 5: Valid Unicode filename with non-ASCII characters is accepted and verified."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            valid_name = "café_測試_файл_🚀.json"
            content = b'{"status": "ok", "experiment": "valid_unicode"}'
            (tmp / valid_name).write_bytes(content)
            chash = hashlib.sha256(content).hexdigest()

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": valid_name, "type": "result_record", "expected_sha256": chash, "size_bytes": len(content)}
            ])))
            res = auditor.audit(str(mpath), str(tmp))
            self.assertEqual(res["totals"][auditor.VERIFIED], 1)
            self.assertEqual(res["totals"][auditor.INVALID], 0)
            art = res["artifacts"][0]
            self.assertEqual(art["status"], auditor.VERIFIED)
            self.assertTrue(art["present"])
            self.assertTrue(art["hash_match"])
            self.assertEqual(art["observed_sha256"], chash)

    def test_6_mixed_manifest_containing_one_valid_and_one_malformed_artifact(self):
        """Case 6: Mixed manifest continues processing other entries when one is invalid."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            valid_name = "valid_evidence.txt"
            content = b"clean_verified_evidence_data"
            (tmp / valid_name).write_bytes(content)
            chash = hashlib.sha256(content).hexdigest()

            bad_nul = "malformed\x00_entry.dat"
            bad_surrogate = f"malformed{chr(0xd800)}_entry.dat"

            mpath = tmp / "manifest.json"
            mpath.write_text(json.dumps(_manifest([
                {"path": valid_name, "type": "trajectory", "expected_sha256": chash, "size_bytes": len(content)},
                {"path": bad_nul, "type": "result_record"},
                {"path": bad_surrogate, "type": "result_record"},
            ])))
            res = auditor.audit(str(mpath), str(tmp))
            self.assertEqual(res["totals"][auditor.VERIFIED], 1)
            self.assertEqual(res["totals"][auditor.INVALID], 2)
            by_path = {a["path"]: a["status"] for a in res["artifacts"]}
            self.assertEqual(by_path[valid_name], auditor.VERIFIED)
            self.assertEqual(by_path[bad_nul], auditor.INVALID)
            self.assertEqual(by_path[bad_surrogate], auditor.INVALID)

    def test_7_invalid_paths_never_reach_filesystem_calls(self):
        """Case 7: Invalid paths with NUL or surrogates never reach filesystem stat/open calls."""
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            mpath = tmp / "manifest.json"
            bad_paths = ["file\x00.txt", f"file{chr(0xd800)}.txt", "nested\x00/file.txt"]
            mpath.write_text(json.dumps(_manifest([
                {"path": p, "type": "result_record"} for p in bad_paths
            ])))

            with patch("os.lstat", wraps=os.lstat) as mock_lstat, patch("os.open", wraps=os.open) as mock_open:
                res = auditor.audit(str(mpath), str(tmp))
                for bad_p in bad_paths:
                    for c in mock_lstat.call_args_list:
                        self.assertNotIn(bad_p, str(c))
                    for c in mock_open.call_args_list:
                        self.assertNotIn(bad_p, str(c))
                self.assertEqual(res["totals"][auditor.INVALID], len(bad_paths))

            # Direct reader invocation also avoids filesystem calls
            with patch("os.lstat", wraps=os.lstat) as mock_lstat, patch("os.open", wraps=os.open) as mock_open:
                for bad_p in bad_paths:
                    status, chash, size, note = auditor._open_and_hash_artifact(tmp, bad_p)
                    self.assertEqual(status, auditor.INVALID)
                    for c in mock_lstat.call_args_list:
                        self.assertNotIn(bad_p, str(c))
                    for c in mock_open.call_args_list:
                        self.assertNotIn(bad_p, str(c))

    def test_8_direct_invocation_of_path_validator_and_artifact_reader(self):
        """Case 8: Path validator and reader return safe results when invoked directly."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            bad_nul = "file\x00.txt"
            bad_high = f"file{chr(0xd800)}.txt"
            bad_low1 = f"file{chr(0xdc00)}.txt"
            bad_low_esc = f"file{chr(0xdc80)}.txt"
            bad_low2 = f"file{chr(0xdfff)}.txt"
            bad_nested_nul = "dir/sub\x00/file.txt"
            bad_nested_surr = f"dir/sub{chr(0xd800)}/file.txt"

            # Validator returns False
            self.assertFalse(auditor._is_safe_relative_path(bad_nul))
            self.assertFalse(auditor._is_safe_relative_path(bad_high))
            self.assertFalse(auditor._is_safe_relative_path(bad_low1))
            self.assertFalse(auditor._is_safe_relative_path(bad_low_esc))
            self.assertFalse(auditor._is_safe_relative_path(bad_low2))
            self.assertFalse(auditor._is_safe_relative_path(bad_nested_nul))
            self.assertFalse(auditor._is_safe_relative_path(bad_nested_surr))

            # Normalizer returns None
            self.assertIsNone(auditor._normalize_relative_path(bad_nul))
            self.assertIsNone(auditor._normalize_relative_path(bad_high))
            self.assertIsNone(auditor._normalize_relative_path(bad_low1))
            self.assertIsNone(auditor._normalize_relative_path(bad_low_esc))

            # Valid Unicode paths accepted
            self.assertTrue(auditor._is_safe_relative_path("café_測試.json"))
            self.assertEqual(auditor._normalize_relative_path("./café_測試.json"), "café_測試.json")

            # Direct reader returns INVALID
            for bad in [bad_nul, bad_high, bad_low1, bad_nested_nul]:
                status, chash, size, note = auditor._open_and_hash_artifact(tmp, bad)
                self.assertEqual(status, auditor.INVALID)
                self.assertIsNone(chash)
                self.assertIsNone(size)
                self.assertIsNotNone(note)

    def test_9_actual_cli_execution_with_malformed_paths(self):
        """Case 9: CLI execution returns exit code 2 without traceback for malformed paths."""
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            for bad_name in ["file\x00.txt", f"file{chr(0xd800)}.txt", "nested\x00/file.txt"]:
                mpath = tmp / "manifest.json"
                out_path = tmp / "out.json"
                sum_path = tmp / "summary.md"
                mpath.write_text(json.dumps(_manifest([
                    {"path": bad_name, "type": "result_record"}
                ])))

                code = auditor.main([
                    str(mpath), str(tmp),
                    "--output", str(out_path),
                    "--summary", str(sum_path),
                ])
                self.assertEqual(code, 2, f"Expected exit code 2 for malformed path: {bad_name!r}")
                self.assertTrue(out_path.exists())
                self.assertTrue(sum_path.exists())
                out_path.unlink()
                sum_path.unlink()

    def test_10_no_traceback_correct_classification_and_expected_exit_code(self):
        """Case 10: Subprocess CLI execution produces no traceback, controlled INVALID, and exit code 2."""
        import subprocess
        with tempfile.TemporaryDirectory() as td:
            tmp = Path(td)
            cases = [
                ("embedded_nul", "bad\x00filename.json"),
                ("unpaired_high_surrogate", f"bad{chr(0xd800)}filename.json"),
                ("unpaired_low_surrogate", f"bad{chr(0xdc00)}filename.json"),
            ]
            for case_label, bad_name in cases:
                mpath = tmp / f"manifest_{case_label}.json"
                mpath.write_text(json.dumps(_manifest([
                    {"path": bad_name, "type": "result_record"}
                ])))
                proc = subprocess.run(
                    [sys.executable, str(_ROOT / "scripts" / "audit_c5_benchmark_evidence.py"), str(mpath), str(tmp)],
                    capture_output=True,
                    text=True,
                )
                self.assertEqual(proc.returncode, 2, f"Case {case_label} must exit with code 2")
                self.assertNotIn("Traceback", proc.stderr, f"Case {case_label} stderr must not contain traceback")
                res = json.loads(proc.stdout)
                self.assertEqual(res["totals"][auditor.INVALID], 1)
                self.assertEqual(res["artifacts"][0]["status"], auditor.INVALID)


# ---------------------------------------------------------------------------
# Security: command-line diagnostics must not disclose untrusted input values
# ---------------------------------------------------------------------------

class TestCliDiagnosticConfidentiality(unittest.TestCase):
    """Keep detailed diagnostics in the structured API, not in terminal logs."""

    def test_invalid_manifest_category_does_not_leak_into_stderr(self):
        import subprocess
        marker = "SENSITIVE_MARKER_CATEGORY_0183"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(_manifest([], expected_sets={marker: {"expected_count": 1}})))
            result = auditor.audit(str(manifest), str(root))
            self.assertTrue(any(marker in err for err in result["errors"]))
            proc = subprocess.run(
                [sys.executable, str(_ROOT / "scripts" / "audit_c5_benchmark_evidence.py"), str(manifest), str(root)],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("audit error: manifest is unreadable or invalid", proc.stderr)
            self.assertNotIn(marker, proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

    def test_unreadable_trusted_config_path_does_not_leak_into_stderr(self):
        import subprocess
        marker = "SENSITIVE_MARKER_TRUSTED_PATH_0294"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(_manifest([])))
            trusted_path = root / (marker + ".json")
            proc = subprocess.run(
                [sys.executable, str(_ROOT / "scripts" / "audit_c5_benchmark_evidence.py"), str(manifest), str(root),
                 "--trusted-expected-sets", str(trusted_path)],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("audit error: invalid trusted expected-sets configuration", proc.stderr)
            self.assertNotIn(marker, proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)

    def test_preflight_conflict_does_not_leak_path_or_modify_manifest(self):
        import subprocess
        marker = "SENSITIVE_MARKER_DESTINATION_0312"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / (marker + ".json")
            manifest.write_text(json.dumps(_manifest([])))
            original = manifest.read_bytes()
            proc = subprocess.run(
                [sys.executable, str(_ROOT / "scripts" / "audit_c5_benchmark_evidence.py"), str(manifest), str(root),
                 "--output", str(manifest)],
                capture_output=True, text=True,
            )
            self.assertEqual(proc.returncode, 2)
            self.assertIn("preflight error: unsafe or conflicting output destination", proc.stderr)
            self.assertNotIn(marker, proc.stderr)
            self.assertNotIn("Traceback", proc.stderr)
            self.assertEqual(manifest.read_bytes(), original)

    def test_publication_error_does_not_leak_exception_text(self):
        from contextlib import redirect_stderr
        from io import StringIO
        from unittest.mock import patch
        marker = "SENSITIVE_MARKER_PUBLISH_0489"
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(_manifest([])))
            for option, expected in (
                ("--output", "publication error: unable to write output"),
                ("--summary", "publication error: unable to write summary"),
            ):
                stderr = StringIO()
                with patch.object(auditor, "_publish_file_securely", side_effect=OSError(marker)):
                    with redirect_stderr(stderr):
                        code = auditor.main([str(manifest), str(root), option, str(root / "output.txt")])
                self.assertEqual(code, 2)
                self.assertIn(expected, stderr.getvalue())
                self.assertNotIn(marker, stderr.getvalue())
                self.assertNotIn("Traceback", stderr.getvalue())


# ---------------------------------------------------------------------------
# Markdown report integrity: untrusted values must remain within their cells.
# ---------------------------------------------------------------------------

class TestMarkdownReportIntegrity(unittest.TestCase):
    def test_newline_and_pipe_in_missing_path_cannot_forge_verified_row(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            bad_name = "not-found\n| apparent-success | result_record | **VERIFIED** | yes | 20 |"
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(_manifest([{
                "path": bad_name, "type": "result_record"
            }])))
            result = auditor.audit(str(manifest), str(root), deterministic=True)
            self.assertEqual(result["artifacts"][0]["status"], auditor.MISSING)
            summary = auditor._markdown_summary(result)
            artifact_section = summary.split("## Artifacts\n", 1)[1].split("## Reproducibility Prerequisites", 1)[0]
            rows = [row for row in artifact_section.splitlines() if row.startswith("|")]
            self.assertEqual(len(rows), 3)
            self.assertNotIn("\n| apparent-success", summary)
            self.assertIn(r"\| apparent-success", summary)
            self.assertIn("**MISSING**", summary)

    def test_code_delimiters_and_html_are_escaped(self):
        value = "bad`</code><img src=x onerror=alert(1)>|name"
        safe = auditor._markdown_code(value)
        self.assertEqual(safe, '` bad `&#96;` </code><img src=x onerror=alert(1)>|name `')
        self.assertEqual(auditor._markdown_code(value, table_cell=True),
                         '` bad `&#96;` </code><img src=x onerror=alert(1)>\\|name `')

    def test_campaign_and_notes_cannot_insert_headings_or_columns(self):
        result = {
            "campaign_id": "benchmark\n## Forged status",
            "root": "/tmp/a|b",
            "manifest_valid": True,
            "totals": {status: 0 for status in auditor.ALL_STATUSES} | {"total": 0},
            "reproducibility_prerequisites": {"raw_trajectories": auditor.INCOMPLETE},
            "reproducibility_details": {"raw_trajectories": {"notes": "missing\n| VERIFIED | yes |"}},
        }
        summary = auditor._markdown_summary(result)
        self.assertNotIn("\n## Forged status", summary)
        prerequisite_section = summary.split("## Reproducibility Prerequisites", 1)[1]
        self.assertNotIn("\n| VERIFIED", prerequisite_section)
        self.assertIn("benchmark\\n## Forged status", summary)
        self.assertIn(r"missing\n\| VERIFIED", summary)

    def test_cli_published_summary_does_not_forge_missing_artifact(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            missing = "missing\n| forged | result_record | **VERIFIED** | yes | 99 |"
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(_manifest([{"path": missing, "type": "result_record"}])))
            output = root / "audit.json"
            summary = root / "audit.md"
            code = auditor.main([str(manifest), str(root), "--output", str(output), "--summary", str(summary), "--deterministic"])
            self.assertEqual(code, 1)
            self.assertEqual(json.loads(output.read_text())["artifacts"][0]["status"], auditor.MISSING)
            text = summary.read_text()
            section = text.split("## Artifacts\n", 1)[1].split("## Reproducibility Prerequisites", 1)[0]
            self.assertEqual(sum(line.startswith("|") for line in section.splitlines()), 3)
            self.assertNotIn("\n| forged", text)
            self.assertIn("**MISSING**", text)

    def test_unicode_line_separators_do_not_create_new_rows(self):
        for separator in (chr(0x85), chr(0x2028), chr(0x2029)):
            safe = auditor._markdown_display_value("before" + separator + "after")
            self.assertNotIn(separator, safe)
            self.assertIn("after", safe)

    def test_unicode_and_surrogates_remain_publishable(self):
        text = auditor._markdown_code("café_測試_🚀" + chr(0xd800))
        self.assertIn("café", text)
        self.assertIn("測試", text)
        self.assertIn("\\ud800", text)
        text.encode("utf-8")


# ---------------------------------------------------------------------------
# Markdown inline safety: untrusted text uses native code spans.
# ---------------------------------------------------------------------------

class TestMarkdownInlineSafety(unittest.TestCase):
    def test_code_field_escapes_link_image_emphasis_and_backticks(self):
        malicious = (
            "[**VERIFIED**](https://example.invalid/track) "
            "![beacon](https://example.invalid/beacon.png) "
            "`fake_code`"
        )
        safe = auditor._markdown_code(malicious)
        self.assertEqual(safe, "` " + malicious.split("`")[0] + " `&#96;` fake_code `&#96;")

    def test_report_renders_potential_links_as_literal_syntax(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            malicious_path = "[VERIFIED](https://example.invalid/track)"
            malicious_campaign = "![beacon](https://example.invalid/collect.png)"
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(_manifest(
                [{"path": malicious_path, "type": "result_record"}],
                campaign_id=malicious_campaign,
            )))
            result = auditor.audit(str(manifest), str(root), deterministic=True)
            self.assertEqual(result["artifacts"][0]["status"], auditor.MISSING)
            summary = auditor._markdown_summary(result)
            self.assertIn("` " + malicious_path + " `", summary)
            self.assertIn("` " + malicious_campaign + " `", summary)
            self.assertIn("**MISSING**", summary)

    def test_raw_url_email_and_strikethrough_are_not_active_markdown(self):
        value = "https://example.invalid/track www.example.invalid mail@example.invalid ~~VERIFIED~~"
        safe = auditor._markdown_code(value)
        self.assertEqual(safe, "` " + value + " `")

    def test_bidi_formatting_controls_cannot_reorder_display(self):
        for codepoint in (0x061C, 0x200E, 0x200F, 0x202A, 0x202B, 0x202C,
                          0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069):
            value = "start" + chr(codepoint) + "finish"
            escaped = auditor._markdown_display_value(value)
            self.assertNotIn(chr(codepoint), escaped)
            self.assertIn(f"\\u{codepoint:04x}", escaped)
            self.assertIn("finish", escaped)



_MARKDOWN_CASES = (
    ("[VERIFIED](https://example.invalid)", "[VERIFIED](https://example.invalid)"),
    ("![image](https://example.invalid/i.png)", "![image](https://example.invalid/i.png)"),
    ("https://example.invalid/a", "https://example.invalid/a"),
    ("http://example.invalid/a", "http://example.invalid/a"),
    ("www.example.invalid", "www.example.invalid"),
    ("eval_user@example.invalid", "eval_user@example.invalid"),
    ("<mailto:user@example.invalid>", "<mailto:user@example.invalid>"),
    ("mailto:user@example.invalid", "mailto:user@example.invalid"),
    ("&#64; &#46; &#124; &#96;", "&#64; &#46; &#124; &#96;"),
    ("&#x40; &#x2e; &#x7c; &#x60;", "&#x40; &#x2e; &#x7c; &#x60;"),
    ("user&#64;example&#46;invalid", "user&#64;example&#46;invalid"),
    ("&lt;img&gt; &amp; &commat; &vert;", "&lt;img&gt; &amp; &commat; &vert;"),
    ("**VERIFIED** *em* _em_ __strong__", "**VERIFIED** *em* _em_ __strong__"),
    ("~~VERIFIED~~", "~~VERIFIED~~"),
    ("` `` ``` **VERIFIED**", "` `` ``` **VERIFIED**"),
    ("`", "`"),
    ("````", "````"),
    (r"a|b\|c\\|d|", r"a|b\|c\\|d|"),
    ("a\n| forged |\r\n## Forged\rheading", r"a\n| forged |\r\n## Forged\rheading"),
    ("a\u0085b\u2028c\u2029d", r"a\u0085b\u2028c\u2029d"),
    ("a\u202eb\u2066c\u2069d", r"a\u202eb\u2066c\u2069d"),
    ("a\ud800\udcffz", r"a\ud800\udcffz"),
    ('</code><img src="x" onerror="alert(1)"><span>fake</span>',
     '</code><img src="x" onerror="alert(1)"><span>fake</span>'),
    ("a\x00b\t\x1b\x7f", r"a\x00b\t\x1b\x7f"),
    ("  leading and trailing  ", "  leading and trailing  "),
    ("   ", "   "),
    ("", ""),
    ("\\[link](https://example.invalid)\\", "\\[link](https://example.invalid)\\"),
    ("café_測試_" + "long." * 250, "café_測試_" + "long." * 250),
)


def _markdown_fixture(payload: str) -> dict:
    return {
        "campaign_id": payload, "root": payload,
        "audited_at": "1970-01-01T00:00:00Z", "manifest_valid": True,
        "timestamp_kind": "deterministic_placeholder",
        "totals": {s: 0 for s in auditor.ALL_STATUSES} | {"total": 1, auditor.MISSING: 1},
        "errors": [payload],
        "artifacts": [{"path": payload, "type": payload, "status": auditor.MISSING,
                       "present": False, "size_bytes": None}],
        "reproducibility_prerequisites": {"raw_trajectories": auditor.INCOMPLETE},
        "reproducibility_details": {"raw_trajectories": {
            "artifact_integrity": auditor.MISSING, "expected_set_status": "INCOMPLETE",
            "prerequisite_complete": False, "notes": payload,
        }},
    }


class TestMarkdownNativeSpans(unittest.TestCase):
    def test_display_values_preserve_punctuation_and_expose_controls(self):
        for value, display in _MARKDOWN_CASES:
            with self.subTest(value=value):
                self.assertEqual(auditor._markdown_display_value(value), display)

    def test_code_and_text_use_the_same_quoting_in_each_context(self):
        for value, _ in _MARKDOWN_CASES:
            for table_cell in (False, True):
                with self.subTest(value=value, table_cell=table_cell):
                    self.assertEqual(auditor._markdown_text(value, table_cell=table_cell),
                                     auditor._markdown_code(value, table_cell=table_cell))

    def test_backticks_are_literal_entities_between_short_spans(self):
        for length in (1, 2, 3, 8, 64, 256, 10000):
            value = "`" * length + " text " + "`" * length
            self.assertEqual(auditor._markdown_code(value),
                             "&#96;" * length + "`  text  `" + "&#96;" * length)

    def test_table_pipe_escape_preserves_existing_backslashes(self):
        for length in range(16):
            value = "a" + "\\" * length + "|b"
            self.assertEqual(auditor._markdown_code(value), "` " + value + " `")
            self.assertEqual(auditor._markdown_code(value, table_cell=True),
                             "` a" + "\\" * (length + 1) + "|b `")

    def test_empty_and_all_space_values_do_not_gain_padding(self):
        self.assertEqual(auditor._markdown_code(""), "")
        self.assertEqual(auditor._markdown_code("   "), "`   `")
        self.assertEqual(auditor._markdown_code(" x "), "`  x  `")

    def test_reports_quote_each_untrusted_field_without_mutating_json(self):
        for value, _ in _MARKDOWN_CASES:
            result = _markdown_fixture(value)
            before = json.dumps(result, sort_keys=True)
            summary = auditor._markdown_summary(result)
            self.assertIn("- **Campaign**: " + auditor._markdown_code(value), summary)
            self.assertIn("- **Root**: " + auditor._markdown_code(value), summary)
            self.assertIn("- " + auditor._markdown_text(value), summary)
            self.assertIn("| " + auditor._markdown_code(value, table_cell=True) + " | "
                          + auditor._markdown_text(value, table_cell=True) + " | **MISSING** | no | - |", summary)
            self.assertEqual(json.dumps(result, sort_keys=True), before)

    def test_cli_json_and_markdown_are_deterministic(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps(_manifest([
                {"path": "missing|user@example.invalid", "type": "~~VERIFIED~~"},
            ], campaign_id="user@example.invalid")))
            outputs = []
            for index in range(2):
                output, summary = root / f"{index}.json", root / f"{index}.md"
                self.assertEqual(auditor.main([str(manifest), str(root), "--output", str(output),
                                              "--summary", str(summary), "--deterministic"]), 1)
                outputs.append((output.read_bytes(), summary.read_bytes()))
            self.assertEqual(outputs[0], outputs[1])


def _run_gfm_checks() -> None:
    # Kept explicit because cmarkgfm is a verification tool, not an app dependency.
    # Import failure must fail this command, rather than silently skip security tests.
    from cmarkgfm import github_flavored_markdown_to_html
    from cmarkgfm.cmark import Options
    from xml.etree import ElementTree as ET

    def render(markdown):
        # Allow raw HTML so a broken escape cannot be hidden by sanitization.
        html = github_flavored_markdown_to_html(markdown, options=Options.CMARK_OPT_UNSAFE)
        return ET.fromstring("<report>" + html + "</report>")

    class TestMarkdownGFM(unittest.TestCase):
        def test_renderer_positive_controls(self):
            dom = render("https://example.invalid user@example.invalid ~~deleted~~ **bold** *em*\n\n"
                         '![image](https://example.invalid/i) <span title="raw">HTML</span>\n\n'
                         "| A | B |\n|---|---|\n| x | y |")
            for tag in ("a", "del", "strong", "em", "img", "span", "table"):
                self.assertIsNotNone(dom.find(".//" + tag), tag)
            self.assertTrue(any(a.get("href", "").startswith("mailto:") for a in dom.iter("a")))
            # Reproduce the original decoded-entity autolink, not just bare URLs.
            original = render("<code>eval&#95;user&#64;example&#46;invalid</code>")
            self.assertEqual(original.find(".//code/a").get("href"), "mailto:eval_user@example.invalid")

        def test_helpers_render_as_literal_text(self):
            for value, display in _MARKDOWN_CASES:
                for helper in (auditor._markdown_code, auditor._markdown_text):
                    for table_cell in (False, True):
                        with self.subTest(value=value, helper=helper.__name__, table_cell=table_cell):
                            quoted = helper(value, table_cell=table_cell)
                            markdown = "| Value |\n|---|\n| " + quoted + " |" if table_cell else quoted
                            dom = render(markdown)
                            parent = dom.find(".//td") if table_cell else dom.find("p")
                            self.assertEqual("".join(parent.itertext()) if parent is not None else "", display)
                            self.assertTrue(all(len(code) == 0 for code in dom.iter("code")))
                            expected_tags = (["report", "table", "thead", "tr", "th", "tbody", "tr", "td"]
                                             if table_cell else (["report", "p"] if display else ["report"]))
                            self.assertEqual([node.tag for node in dom.iter() if node.tag != "code"], expected_tags)

        def test_backtick_lengths_and_backslashes_before_table_pipes(self):
            for length in list(range(65)) + [80, 81, 128, 256, 1000, 10000]:
                value = "`" * length + "a" + "\\" * length + "| **fake** |" + "`" * length
                for table_cell in (False, True):
                    quoted = auditor._markdown_code(value, table_cell=table_cell)
                    markdown = "| A | B |\n|---|---|\n| " + quoted + " | end |" if table_cell else quoted
                    dom = render(markdown)
                    parent = dom.find(".//td") if table_cell else dom.find("p")
                    self.assertEqual("".join(parent.itertext()), value)
                    self.assertTrue(all(len(code) == 0 for code in dom.iter("code")))
                    for tag in ("strong", "em", "a", "del"):
                        self.assertIsNone(dom.find(".//" + tag), tag)
                    if table_cell:
                        self.assertEqual(len(dom.findall(".//tbody/tr")), 1)
                        self.assertEqual(len(dom.findall(".//td")), 2)
                        self.assertEqual(dom.findall(".//td")[1].text, "end")

        def test_every_short_backtick_length_and_very_long_runs(self):
            for value in (
                " ".join("`" * n for n in range(1, 130)) + " https://example.invalid **fake**",
                "`" * 100000 + " user@example.invalid ~~fake~~ " + "`" * 100000,
            ):
                for table_cell in (False, True):
                    quoted = auditor._markdown_code(value, table_cell=table_cell)
                    markdown = "| A |\n|---|\n| " + quoted + " |" if table_cell else quoted
                    dom = render(markdown)
                    parent = dom.find(".//td") if table_cell else dom.find("p")
                    self.assertEqual("".join(parent.itertext()), value)
                    self.assertTrue(all(n.tag in {"report", "p", "code", "table", "thead",
                                                 "tbody", "tr", "th", "td"} for n in dom.iter()))

        def assert_report(self, result):
            summary = auditor._markdown_summary(result)
            dom = render(summary)
            # Compare the entire element structure with the same report using safe text.
            baseline = render(auditor._markdown_summary(_markdown_fixture("safe")))
            self.assertEqual([n.tag for n in dom.iter() if n.tag != "code"],
                             [n.tag for n in baseline.iter() if n.tag != "code"])
            self.assertTrue(all(len(code) == 0 for code in dom.iter("code")))
            tables = dom.findall(".//table")
            self.assertEqual([len(t.findall(".//tbody/tr")) for t in tables], [7, 1, 1])
            self.assertEqual([[len(row) for row in t.findall(".//tr")] for t in tables],
                             [[2] * 8, [5] * 2, [6] * 2])
            self.assertEqual([n.text for n in dom.iter("strong")],
                             ["Campaign", "Audited at", "Timestamp note", "Root", "Manifest valid",
                              auditor.MISSING, auditor.INCOMPLETE])
            return dom

        def test_complete_reports_keep_classification_and_structure(self):
            for value, display in _MARKDOWN_CASES:
                if not value:
                    continue  # Empty cells are checked by the helper test.
                with self.subTest(value=value):
                    dom = self.assert_report(_markdown_fixture(value))
                    items = dom.findall(".//li")
                    self.assertEqual("".join(items[0].itertext()), "Campaign: " + display)
                    self.assertEqual("".join(items[3].itertext()), "Root: " + display)
                    self.assertEqual("".join(items[5].itertext()), display)
                    tables = dom.findall(".//table")
                    cells = tables[1].findall(".//td")
                    self.assertEqual(["".join(c.itertext()) for c in cells],
                                     [display, display, auditor.MISSING, "no", "-"])
                    self.assertEqual("".join(tables[2].findall(".//td")[-1].itertext()), display)

        def test_actual_cli_reports_match_authoritative_json(self):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td) / "`root`|admin@example.invalid"
                root.mkdir()
                manifest = root / "manifest.json"
                path = "missing`|user@example.invalid\n## Forged"
                manifest.write_text(json.dumps(_manifest([
                    {"path": path, "type": "https://example.invalid ~~VERIFIED~~"},
                ], campaign_id="admin@example.invalid")))
                output, summary = root / "audit.json", root / "audit.md"
                self.assertEqual(auditor.main([str(manifest), str(root), "--output", str(output),
                                              "--summary", str(summary), "--deterministic"]), 1)
                result = json.loads(output.read_text())
                self.assertEqual(result["artifacts"][0]["status"], auditor.MISSING)
                dom = render(summary.read_text())
                for tag in ("a", "img", "del", "em", "span", "script"):
                    self.assertIsNone(dom.find(".//" + tag), tag)
                rows = dom.findall(".//table")[1].findall(".//tbody/tr")
                self.assertEqual(len(rows), 1)
                self.assertEqual(["".join(cell.itertext()) for cell in rows[0]],
                                 [r"missing`|user@example.invalid\n## Forged",
                                  result["artifacts"][0]["type"], auditor.MISSING, "no", "-"])
                self.assertEqual(len(dom.findall(".//h1")), 1)
                self.assertEqual(len(dom.findall(".//h2")), 3)
                items = ["".join(n.itertext()) for n in dom.iter("li")]
                self.assertIn("Root: " + str(root), items)
                self.assertIn("Campaign: " + result["campaign_id"], items)

        def test_real_duplicate_diagnostics_and_validation_errors(self):
            with tempfile.TemporaryDirectory() as td:
                root = Path(td)
                path = "user@example.invalid|~~VERIFIED~~"
                entry = {"path": path, "type": "trajectory"}
                result = _run_audit(root, _manifest([entry, entry]), deterministic=True)
                notes = result["reproducibility_details"]["raw_trajectories"]["notes"]
                self.assertIn("duplicate", notes)
                self.assertIn(path, notes)
                dom = render(auditor._markdown_summary(result))
                self.assertIn(notes, [n.text for n in dom.iter("code")])
                bad = _manifest([], expected_sets={"https://example.invalid ~~VERIFIED~~": {}})
                result = _run_audit(root, bad, deterministic=True)
                self.assertTrue(result["errors"])
                dom = render(auditor._markdown_summary(result))
                self.assertIn(result["errors"][0], [n.text for n in dom.iter("code")])
                for tag in ("a", "img", "del", "em"):
                    self.assertIsNone(dom.find(".//" + tag), tag)

    suite = unittest.defaultTestLoader.loadTestsFromTestCase(TestMarkdownGFM)
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())


if __name__ == "__main__":
    if sys.argv[1:] == ["--gfm"]:
        _run_gfm_checks()
    else:
        unittest.main()
