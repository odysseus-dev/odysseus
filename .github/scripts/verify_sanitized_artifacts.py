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
import re
import shutil
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
        p = Path(zp)
        if p.exists():
            try:
                p.unlink(missing_ok=True)
            except OSError:
                pass
        return False

logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")
logger = logging.getLogger("verify_sanitized_artifacts")

PNG_MAGIC = b"\x89PNG\r\n\x1a\n"

ALLOWED_JUNIT_ROOT_TAGS = frozenset({"testsuites", "testsuite"})
ALLOWED_JUNIT_ELEMENT_TAGS = frozenset({
    "testsuites",
    "testsuite",
    "testcase",
    "failure",
    "error",
    "skipped",
    "system-out",
    "system-err",
    "properties",
    "property",
})

SENSITIVE_KEY_PATTERN = re.compile(
    r"(?i)(password|passwd|secret|token|api_key|apikey|access_token|refresh_token|"
    r"private_key|auth_token|client_secret|session|session_id|sid|csrf|xsrf|jwt|credential|authorization)"
)

SECRET_REDACTION_PATTERNS = [
    # 1. Authorization headers (Bearer / Basic)
    re.compile(r"(?i)\b(bearer|basic)\s+([^\s,;\'\"<>]+)"),
    # 2. Sensitive key-value pairs (key=value or key: value)
    re.compile(
        r"(?i)\b([a-z0-9_.-]*(?:password|passwd|secret|token|api_key|apikey|access_token|refresh_token|"
        r"auth_token|client_secret|session|session_id|sid|csrf|xsrf|jwt|credential|authorization)[a-z0-9_.-]*)"
        r"\s*([:=])\s*([^\s,;\'\"<>]+)"
    ),
    # 3. URL credentials (scheme://user:pass@host)
    re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://[^:\s]+):([^@\s]+)@"),
    # 4. JSON-formatted key-value pairs
    re.compile(
        r'(?i)("?(?:[a-z0-9_.-]*(?:password|passwd|secret|token|key|auth)[a-z0-9_.-]*)"?\s*:\s*")([^"]+)(")'
    ),
    # 5. Synthetic secret keywords or tokens
    re.compile(r"(?i)\b[a-z0-9_.-]*synthetic[-_]?[a-z0-9_.-]*\b"),
]


def safe_remove(filepath: str | Path) -> bool:
    """Safely remove a file or directory fail-closed.

    Returns True if the target does not exist or was successfully removed.
    Returns False if removal failed and the target still exists on disk.
    """
    p = Path(filepath)
    if not p.exists():
        return True
    try:
        if p.is_dir():
            shutil.rmtree(p, ignore_errors=False)
        else:
            p.unlink(missing_ok=True)
    except OSError as exc:
        logger.error("Failed to delete unauthorized file %s: %s", filepath, exc)
        return False

    if p.exists():
        logger.error("Target %s still exists on disk after deletion attempt", filepath)
        return False
    return True


def sanitize_text_content(text: str) -> tuple[str, bool]:
    """Scrub sensitive credentials, tokens, and synthetic secrets from text."""
    if not text or not isinstance(text, str):
        return text, False

    modified = False
    out = text

    # 1. Replace Bearer / Basic tokens
    def _sub_auth(m: re.Match) -> str:
        nonlocal modified
        modified = True
        return f"{m.group(1)} [REDACTED]"

    out = SECRET_REDACTION_PATTERNS[0].sub(_sub_auth, out)

    # 2. Replace key-value pairs
    def _sub_kv(m: re.Match) -> str:
        nonlocal modified
        modified = True
        return f"{m.group(1)}{m.group(2)}[REDACTED]"

    out = SECRET_REDACTION_PATTERNS[1].sub(_sub_kv, out)

    # 3. Replace URL credentials
    def _sub_url(m: re.Match) -> str:
        nonlocal modified
        modified = True
        return f"{m.group(1)}:[REDACTED]@"

    out = SECRET_REDACTION_PATTERNS[2].sub(_sub_url, out)

    # 4. Replace JSON key-value secrets
    def _sub_json(m: re.Match) -> str:
        nonlocal modified
        modified = True
        return f"{m.group(1)}[REDACTED]{m.group(3)}"

    out = SECRET_REDACTION_PATTERNS[3].sub(_sub_json, out)

    # 5. Replace synthetic secret markers
    def _sub_synthetic(m: re.Match) -> str:
        nonlocal modified
        modified = True
        return "[REDACTED]"

    out = SECRET_REDACTION_PATTERNS[4].sub(_sub_synthetic, out)

    return out, modified


def sanitize_and_verify_junit_xml(filepath: str | Path) -> bool:
    """Verify that an XML file is an authentic JUnit report and redact credentials.

    Returns True if the XML file is a valid recognized JUnit report and was safely sanitized.
    Returns False if the file is invalid, unrecognized, corrupted, or failed sanitization.
    """
    p = Path(filepath)
    name_lower = p.name.lower()

    # 1. Filename restriction: must match standard JUnit report naming
    if not (name_lower == "junit.xml" or name_lower == "browser-junit.xml" or name_lower.endswith("-junit.xml") or name_lower.endswith(".junit.xml")):
        logger.warning("Unrecognized XML report filename %s; not an authorized JUnit report", filepath)
        return False

    # 2. Parse XML
    try:
        tree = ET.parse(p)
    except Exception as exc:
        logger.warning("Corrupted XML report %s: %s", filepath, exc)
        return False

    root = tree.getroot()
    # 3. Root tag restriction
    if root.tag.lower() not in ALLOWED_JUNIT_ROOT_TAGS:
        logger.warning("XML report %s has invalid root element <%s>; not a JUnit report", filepath, root.tag)
        return False

    # 4. Check element tags and redact sensitive content
    file_modified = False
    for elem in tree.iter():
        if elem.tag.lower() not in ALLOWED_JUNIT_ELEMENT_TAGS:
            logger.warning("XML report %s contains unrecognized element tag <%s>", filepath, elem.tag)
            return False

        # Sanitize attributes
        for k, v in list(elem.attrib.items()):
            if SENSITIVE_KEY_PATTERN.search(k.lower()):
                elem.attrib[k] = "[REDACTED]"
                file_modified = True
            elif isinstance(v, str):
                new_v, changed = sanitize_text_content(v)
                if changed:
                    elem.attrib[k] = new_v
                    file_modified = True

        # Sanitize text
        if elem.text:
            new_text, changed = sanitize_text_content(elem.text)
            if changed:
                elem.text = new_text
                file_modified = True

        # Sanitize tail
        if elem.tail:
            new_tail, changed = sanitize_text_content(elem.tail)
            if changed:
                elem.tail = new_tail
                file_modified = True

    # 5. If content was sanitized, write back safely
    if file_modified:
        try:
            tree.write(str(p), encoding="utf-8", xml_declaration=True)
            # Verify rewritten XML parses cleanly
            ET.parse(p)
        except Exception as exc:
            logger.error("Failed to write sanitized XML report %s: %s", filepath, exc)
            return False

    return True


def is_valid_disposable_ci_fixture(target_dir: str | Path | None = None) -> bool:
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

    allowed_tmp = [os.path.abspath(p) for p in (tempfile.gettempdir(), "/tmp", "/var/tmp")]

    # Check 1: Verify fixture ownership via PHOTO_EDITOR_E2E_DATA_DIR
    data_dir = os.environ.get("PHOTO_EDITOR_E2E_DATA_DIR")
    if data_dir:
        resolved = os.path.abspath(data_dir)
        base_name = os.path.basename(resolved)
        is_tmp = any(resolved.startswith(b + os.sep) or resolved == b for b in allowed_tmp)
        if base_name.startswith("odysseus-photo-editor-e2e-") and is_tmp:
            expected_db = os.path.join(resolved, "app.db")
            db_path = os.environ.get("PHOTO_EDITOR_E2E_DB_PATH")
            if not db_path or db_path == expected_db:
                marker_path = os.path.join(resolved, ".disposable-ci-fixture.json")
                if os.path.exists(marker_path):
                    try:
                        with open(marker_path, "r", encoding="utf-8") as f:
                            fixture = json.load(f)
                            if (
                                fixture.get("synthetic") is True
                                and fixture.get("authenticated") is True
                                and fixture.get("database") == expected_db
                            ):
                                return True
                    except Exception:
                        pass

    # Check 2: Verify fixture proof in target_dir if provided
    if target_dir:
        target_marker = os.path.join(str(target_dir), ".disposable-ci-fixture.json")
        if os.path.exists(target_marker):
            try:
                with open(target_marker, "r", encoding="utf-8") as f:
                    fixture = json.load(f)
                    db_in_fixture = fixture.get("database", "")
                    resolved_db = os.path.abspath(db_in_fixture)
                    db_dir = os.path.dirname(resolved_db)
                    db_base = os.path.basename(db_dir)
                    is_tmp = any(db_dir.startswith(b + os.sep) or db_dir == b for b in allowed_tmp)
                    if (
                        fixture.get("synthetic") is True
                        and fixture.get("authenticated") is True
                        and db_base.startswith("odysseus-photo-editor-e2e-")
                        and is_tmp
                        and os.path.basename(resolved_db) == "app.db"
                    ):
                        return True
            except Exception:
                pass

    return False


def verify_and_sanitize_artifacts(target_dir: str) -> bool:
    """Independently verify and sanitize diagnostics fail-closed, preserving safe JUnit XML.

    Returns True if verification succeeded and artifacts are safe for publication.
    Returns False if verification failed or deletion could not be confirmed.
    """
    if not os.path.exists(target_dir):
        logger.info("Target directory %s does not exist; nothing to sanitize.", target_dir)
        return True

    disposable_ci = is_valid_disposable_ci_fixture(target_dir)
    preserved_xmls = 0
    sanitized_archives = 0
    purged_files = 0
    overall_success = True

    try:
        for root, dirs, files in os.walk(target_dir, topdown=False):
            for filename in files:
                filepath = os.path.join(root, filename)
                name_lower = filename.lower()

                # Cleanup disposable fixture marker so it is not published
                if filename == ".disposable-ci-fixture.json":
                    if not safe_remove(filepath):
                        overall_success = False
                    continue

                # 1. XML reports: verify JUnit structure and sanitize credentials
                if name_lower.endswith(".xml"):
                    if sanitize_and_verify_junit_xml(filepath):
                        preserved_xmls += 1
                        continue
                    else:
                        logger.warning("Rejecting invalid/unrecognized XML report %s; purging fail-closed", filepath)
                        if not safe_remove(filepath):
                            overall_success = False
                        purged_files += 1
                        continue

                # 2. Check if diagnostics are authorized (disposable synthetic fixture)
                if not disposable_ci:
                    logger.info("Purging diagnostic artifact %s (disposable CI fixture not verified)", filepath)
                    if not safe_remove(filepath):
                        overall_success = False
                    purged_files += 1
                    continue

                # 3. Disposable CI is authorized: enforce strict sanitization
                if name_lower.endswith(".zip"):
                    ok = sanitize_trace_archive(filepath)
                    if ok and os.path.exists(filepath):
                        sanitized_archives += 1
                    else:
                        logger.warning("Trace archive %s failed sanitization; purging fail-closed", filepath)
                        if not safe_remove(filepath):
                            overall_success = False
                        purged_files += 1

                elif name_lower.endswith(".png"):
                    # Validate PNG magic bytes to ensure not a secret payload disguised as an image
                    is_png = False
                    try:
                        with open(filepath, "rb") as pf:
                            header = pf.read(8)
                            if header == PNG_MAGIC:
                                is_png = True
                    except Exception:
                        is_png = False

                    if is_png:
                        continue

                    logger.warning("Invalid or suspicious screenshot %s; purging fail-closed", filepath)
                    if not safe_remove(filepath):
                        overall_success = False
                    purged_files += 1

                else:
                    # Purge any .tmp, .har, .db, .sqlite, .log or unrecognized files
                    logger.info("Purging unauthorized diagnostic file %s", filepath)
                    if not safe_remove(filepath):
                        overall_success = False
                    purged_files += 1

            # Remove empty subdirectories
            for d in dirs:
                dirpath = os.path.join(root, d)
                try:
                    if not os.listdir(dirpath):
                        os.rmdir(dirpath)
                except OSError:
                    pass

        if not overall_success:
            logger.error("Verification failed: one or more unauthorized files could not be purged.")
            return False

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
                    safe_remove(p)
        except Exception:
            pass
        return False


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("target_dir", nargs="?", default="test-results", help="Directory containing test results")
    args = parser.parse_args()

    ok = verify_and_sanitize_artifacts(args.target_dir)
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
