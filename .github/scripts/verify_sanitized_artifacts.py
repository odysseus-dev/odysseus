#!/usr/bin/env python3
"""Fail-closed independent verification and sanitization gate for test diagnostics.

Runs after test suite completion or abnormal runner termination (e.g. failures, timeouts, crashes).
Ensures that no unsanitized traces, raw credentials, temporary archives, or unauthorized
diagnostics can ever be published as CI artifacts, while preserving safe JUnit XML reporting.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

# Add repository root to Python path to import sanitizer
REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

try:
    from scripts.sanitize_trace_artifacts import sanitize_trace_archive
except ImportError:
    # If import fails, define fail-closed fallback
    def sanitize_trace_archive(zp: str) -> bool:
        if os.path.exists(zp):
            try:
                os.remove(zp)
            except OSError:
                pass
        return False

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("verify_sanitized_artifacts")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def is_valid_disposable_ci_fixture() -> bool:
    """Verify that execution ran inside an authorized disposable synthetic fixture."""
    is_ci = bool(os.environ.get("CI") or os.environ.get("GITHUB_ACTIONS"))
    if not is_ci:
        # Local runs require explicit trace opt-in
        trace_opt_in = os.environ.get("PLAYWRIGHT_ENABLE_TRACE", "").lower() in ("1", "true")
        return trace_opt_in

    if os.environ.get("ODYSSEUS_E2E_DISPOSABLE_ARTIFACTS") != "1":
        return False
    if os.environ.get("PHOTO_EDITOR_E2E_AUTH") == "0":
        return False

    data_dir = os.environ.get("PHOTO_EDITOR_E2E_DATA_DIR")
    if not data_dir:
        return False
    resolved = os.path.abspath(data_dir)
    base_name = os.path.basename(resolved)
    if not base_name.startswith("odysseus-photo-editor-e2e-"):
        return False

    allowed_tmp = [os.path.abspath(p) for p in (tempfile.gettempdir(), "/tmp", "/var/tmp")]
    if not any(resolved.startswith(b + os.sep) or resolved == b for b in allowed_tmp):
        return False

    db_path = os.environ.get("PHOTO_EDITOR_E2E_DB_PATH")
    if db_path != os.path.join(resolved, "app.db"):
        return False

    marker_path = os.path.join(resolved, ".disposable-ci-fixture.json")
    if not os.path.exists(marker_path):
        return False

    try:
        with open(marker_path, "r", encoding="utf-8") as f:
            fixture = json.load(f)
            return (
                fixture.get("synthetic") is True
                and fixture.get("authenticated") is True
                and fixture.get("database") == os.path.join(resolved, "app.db")
            )
    except Exception:
        return False


def verify_and_sanitize_artifacts(target_dir: str) -> bool:
    """Independently verify and sanitize diagnostics fail-closed, preserving safe JUnit XML.

    Returns True if verification succeeded, False if fail-closed purge was required.
    """
    if not os.path.exists(target_dir):
        logger.info("Target directory %s does not exist; nothing to sanitize.", target_dir)
        return True

    disposable_ci = is_valid_disposable_ci_fixture()
    preserved_xmls = 0
    sanitized_archives = 0
    purged_files = 0

    try:
        for root, _, files in os.walk(target_dir):
            for filename in files:
                filepath = os.path.join(root, filename)
                name_lower = filename.lower()

                # 1. Safe JUnit reporting: preserve valid XML reports
                if name_lower.endswith(".xml"):
                    try:
                        ET.parse(filepath)
                        preserved_xmls += 1
                        continue
                    except Exception as exc:
                        logger.warning("Corrupted XML report %s: %s; purging fail-closed", filepath, exc)
                        try:
                            os.remove(filepath)
                            purged_files += 1
                        except OSError:
                            pass
                        continue

                # 2. Check if diagnostics are authorized (disposable synthetic fixture)
                if not disposable_ci:
                    # In CI without disposable fixture, or local without opt-in: purge all diagnostics
                    logger.info("Purging diagnostic artifact %s (disposable CI fixture not verified)", filepath)
                    try:
                        os.remove(filepath)
                        purged_files += 1
                    except OSError:
                        pass
                    continue

                # 3. Disposable CI is authorized: enforce strict sanitization
                if name_lower.endswith(".zip"):
                    ok = sanitize_trace_archive(filepath)
                    if ok and os.path.exists(filepath):
                        sanitized_archives += 1
                    else:
                        logger.warning("Trace archive %s failed sanitization; purged fail-closed", filepath)
                        if os.path.exists(filepath):
                            try:
                                os.remove(filepath)
                            except OSError:
                                pass
                        purged_files += 1

                elif name_lower.endswith(".png"):
                    # Validate PNG magic bytes to ensure not a secret payload disguised as an image
                    try:
                        with open(filepath, "rb") as pf:
                            header = pf.read(8)
                            if header == PNG_MAGIC:
                                continue
                    except Exception:
                        pass
                    logger.warning("Invalid or suspicious screenshot %s; purged fail-closed", filepath)
                    try:
                        os.remove(filepath)
                        purged_files += 1
                    except OSError:
                        pass

                else:
                    # Purge any .tmp, .har, .db, .sqlite, .log or unrecognized files
                    logger.info("Purging unauthorized diagnostic file %s", filepath)
                    try:
                        os.remove(filepath)
                        purged_files += 1
                    except OSError:
                        pass

        logger.info(
            "Artifact verification complete for %s: preserved %d XML reports, sanitized %d archives, purged %d files.",
            target_dir,
            preserved_xmls,
            sanitized_archives,
            purged_files,
        )
        return True

    except Exception as exc:
        logger.error("Unexpected error during artifact verification: %s; purging all non-XML files fail-closed", exc)
        try:
            for p in Path(target_dir).rglob("*"):
                if p.is_file() and not p.name.lower().endswith(".xml"):
                    try:
                        p.unlink(missing_ok=True)
                    except OSError:
                        pass
        except Exception:
            pass
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_dir", nargs="?", default="test-results", help="Directory containing test results")
    args = parser.parse_args()

    ok = verify_and_sanitize_artifacts(args.target_dir)
    # Always exit 0 so upload-artifact can safely publish verified reports
    sys.exit(0 if ok else 0)


if __name__ == "__main__":
    main()
