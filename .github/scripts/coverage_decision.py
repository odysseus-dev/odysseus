#!/usr/bin/env python3
"""Evaluate selective CI coverage policy and validate native test inventory.

Determines whether native Node and Chromium browser suites are required
for a given change set, following the three-level CI architecture:
- Level A: Fast inventory, ownership, and policy verification on every PR.
- Level B: Selective PR validation based on dependency structure.
- Level C: Full integration validation on pushes to dev/main and manual dispatches.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from collections.abc import Iterable
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
MANIFEST_PATH = ROOT / "tests" / "native-ci-manifest.json"

# Categorization path sets and patterns
DOCS_PATTERNS = (
    re.compile(r"^docs/"),
    re.compile(r"^website/"),
    re.compile(r"^[A-Z0-9_-]+\.md$"),
    re.compile(r"^LICENSE$"),
    re.compile(r"^\.gitignore$"),
    re.compile(r"^\.gitattributes$"),
    re.compile(r"^mkdocs\.ya?ml$"),
)

PHOTO_EDITOR_PATTERNS = (
    re.compile(r"^static/js/galleryEditor\.js$"),
    re.compile(r"^static/js/gallery\.js$"),
    re.compile(r"^static/js/editor/"),
    re.compile(r"^tests/e2e/photo-editor/"),
    re.compile(r"^tests/e2e/playwright\.config\.js$"),
    re.compile(r"^tests/e2e/setup\.js$"),
    re.compile(r"^tests/e2e/teardown\.js$"),
    re.compile(r"^tests/e2e/artifact-policy\.js$"),
    re.compile(r"^tests/chatPhotoEditorIntegrity\.test\.mjs$"),
    re.compile(r"^tests/galleryModelPickerSync\.test\.mjs$"),
)

SHARED_FRONTEND_PATTERNS = (
    re.compile(r"^static/app\.js$"),
    re.compile(r"^static/index\.html$"),
    re.compile(r"^static/css/"),
    re.compile(r"^static/js/shared/"),
    re.compile(r"^static/js/ui\.js$"),
    re.compile(r"^static/js/theme\.js$"),
    re.compile(r"^static/js/modelPicker\.js$"),
    re.compile(r"^static/js/providers\.js$"),
    re.compile(r"^static/js/storage\.js$"),
    re.compile(r"^frontend/"),
    re.compile(r"^package\.json$"),
    re.compile(r"^package-lock\.json$"),
    re.compile(r"^scripts/build_frontend\.py$"),
    re.compile(r"^scripts/check_frontend\.mjs$"),
)

BROWSER_FACING_BACKEND_PATTERNS = (
    re.compile(r"^app\.py$"),
    re.compile(r"^core/"),
    re.compile(r"^routes/"),
    re.compile(r"^services/"),
    re.compile(r"^src/app_initializer\.py$"),
    re.compile(r"^src/app_helpers\.py$"),
    re.compile(r"^src/config\.py$"),
    re.compile(r"^src/constants\.py$"),
    re.compile(r"^src/task_scheduler\.py$"),
    re.compile(r"^src/process_reaper\.py$"),
    re.compile(r"^src/bg_monitor\.py$"),
    re.compile(r"^src/generated_images\.py$"),
    re.compile(r"^src/owner_identity\.py$"),
    re.compile(r"^src/mcp_manager\.py$"),
    re.compile(r"^src/agent_tools\.py$"),
    re.compile(r"^src/rag_singleton\.py$"),
    re.compile(r"^src/upload_handler\.py$"),
    re.compile(r"^src/event_bus\.py$"),
    re.compile(r"^tests/emailReplyBrowser\.test\.mjs$"),
)

CI_TEST_INFRA_PATTERNS = (
    re.compile(r"^\.github/workflows/native-test-suites\.ya?ml$"),
    re.compile(r"^\.github/workflows/ci\.ya?ml$"),
    re.compile(r"^\.github/scripts/run_native_tests\.mjs$"),
    re.compile(r"^\.github/scripts/coverage_decision\.py$"),
    re.compile(r"^\.github/scripts/check_test_execution\.py$"),
    re.compile(r"^\.github/scripts/verify_sanitized_artifacts\.py$"),
    re.compile(r"^scripts/sanitize_trace_artifacts\.py$"),
    re.compile(r"^tests/native-ci-manifest\.json$"),
    re.compile(r"^tests/test_verify_sanitized_artifacts\.py$"),
    re.compile(r"^tests/test_playwright_teardown_security\.py$"),
    re.compile(r"^tests/test_trace_artifact_sanitizer\.py$"),
    re.compile(r"^tests/test_coverage_decision\.py$"),
)

NATIVE_NODE_TEST_PATTERNS = (
    re.compile(r"^tests/.*\.test\.mjs$"),
    re.compile(r"^tests/streaming/"),
    re.compile(r"^tests/.*\.behavior\.mjs$"),
)

ISOLATED_BACKEND_PYTEST_PATTERNS = (
    re.compile(r"^tests/test_"),
    re.compile(r"^tests/helpers/"),
    re.compile(r"^tests/cli/"),
    re.compile(r"^src/cli/"),
    re.compile(r"^scripts/check_ca_trust\.py$"),
    re.compile(r"^scripts/benchmarks/"),
    re.compile(r"^src/personal_docs\.py$"),
    re.compile(r"^src/search_providers/"),
    re.compile(r"^src/memory_vector\.py$"),
    re.compile(r"^src/embeddings\.py$"),
    re.compile(r"^src/chroma_client\.py$"),
)


def normalize_repo_path(raw_path: str) -> str:
    path = PurePosixPath(raw_path.strip())
    parts = tuple(p for p in path.parts if p != ".")
    return PurePosixPath(*parts).as_posix()


def parse_diff_paths(diff_output: str) -> list[str]:
    """Parse output of git diff --name-status -M."""
    paths: set[str] = set()
    for line in diff_output.splitlines():
        line = line.strip()
        if not line:
            continue
        tokens = line.split("\t")
        if not tokens:
            continue
        # For renames/copies (Rxxx / Cxxx), record both old and new paths
        for path_token in tokens[1:]:
            paths.add(normalize_repo_path(path_token))
    return sorted(paths)


def discover_native_tests(repo_root: Path = ROOT) -> list[str]:
    """Discover all test files under tests/ that import node:test."""
    script = """
import fs from 'node:fs';
import path from 'node:path';
import { parse } from 'acorn';

const root = process.cwd();
function files(directory) {
  return fs.readdirSync(directory, { withFileTypes: true }).flatMap(entry => {
    const target = path.join(directory, entry.name);
    return entry.isDirectory() ? files(target) : /\\.(?:mjs|cjs|js)$/.test(entry.name) ? [target] : [];
  });
}
function importsNativeTest(node) {
  if (!node || typeof node !== 'object') return false;
  if (node.type === 'ImportDeclaration' && node.source.value === 'node:test') return true;
  if (node.type === 'CallExpression' && node.callee.name === 'require' && node.arguments[0]?.value === 'node:test') return true;
  return Object.values(node).some(value => Array.isArray(value) ? value.some(importsNativeTest) : importsNativeTest(value));
}
const discovered = files(path.join(root, 'tests')).filter(file => {
  const source = fs.readFileSync(file, 'utf8');
  try {
    return source.includes('node:test') && importsNativeTest(parse(source, { ecmaVersion: 'latest', sourceType: 'module' }));
  } catch (e) {
    return false;
  }
}).map(file => path.relative(root, file).split(path.sep).join('/')).sort();
console.log(JSON.stringify(discovered));
"""
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=True,
    )
    return json.loads(res.stdout)


def validate_test_manifest(
    manifest_path: Path = MANIFEST_PATH, repo_root: Path = ROOT
) -> tuple[bool, str, dict]:
    """Validate that tests/native-ci-manifest.json is exhaustive and disjoint."""
    if not manifest_path.exists():
        return False, f"Manifest file not found: {manifest_path}", {}

    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return False, f"Failed to parse manifest JSON: {exc}", {}

    discovered = discover_native_tests(repo_root)
    assigned_files: list[str] = []

    for key in ("native-ci", "browser-ci", "architecture-ci", "ci-syntax"):
        items = manifest.get(key, [])
        if not isinstance(items, list):
            return False, f"Manifest group {key} must be a list", {}
        assigned_files.extend(items)

    behavior_delegates = manifest.get("behavior-delegates", {})
    if not isinstance(behavior_delegates, dict):
        return False, "Manifest behavior-delegates must be a dictionary", {}
    assigned_files.extend(behavior_delegates.keys())

    python_ci = manifest.get("python-ci", {})
    if not isinstance(python_ci, dict):
        return False, "Manifest python-ci must be a dictionary", {}
    assigned_files.extend(python_ci.keys())

    # Check for duplicates within assigned
    if len(assigned_files) != len(set(assigned_files)):
        seen = set()
        dupes = [x for x in assigned_files if x in seen or seen.add(x)]
        return False, f"Duplicate assignments in manifest: {dupes}", {}

    # Check exhaustiveness against discovered
    missing_from_manifest = sorted(set(discovered) - set(assigned_files))
    if missing_from_manifest:
        return (
            False,
            f"Test files missing from manifest ({len(missing_from_manifest)}): {missing_from_manifest}",
            {},
        )

    extra_in_manifest = sorted(set(assigned_files) - set(discovered))
    if extra_in_manifest:
        return (
            False,
            f"Manifest contains test files not discovered in repository: {extra_in_manifest}",
            {},
        )

    # Validate pytest wrapper files and invocation integrity
    for test_file, wrappers in python_ci.items():
        if not wrappers:
            return False, f"No pytest wrappers declared for {test_file}", {}
        test_stem = Path(test_file).stem.split(".")[0]
        test_name = Path(test_file).name
        for wrapper in wrappers:
            w_path = repo_root / wrapper
            if not w_path.exists():
                return False, f"Declared pytest wrapper does not exist: {wrapper}", {}
            content = w_path.read_text(encoding="utf-8")
            if test_stem not in content and test_name not in content:
                return (
                    False,
                    f"Wrapper {wrapper} does not reference {test_file}",
                    {},
                )
            if not any(
                term in content for term in ("subprocess", "execFile", "spawn", "run(")
            ):
                return (
                    False,
                    f"Wrapper {wrapper} declares {test_file} but has no execution invocation",
                    {},
                )

    # Validate behavior delegates
    for beh_file, parent_file in behavior_delegates.items():
        p_path = repo_root / parent_file
        if not p_path.exists():
            return (
                False,
                f"Parent runner for behavior delegate does not exist: {parent_file}",
                {},
            )
        p_content = p_path.read_text(encoding="utf-8")
        if Path(beh_file).name not in p_content:
            return (
                False,
                f"Parent runner {parent_file} does not invoke behavior delegate {beh_file}",
                {},
            )

    return True, f"Manifest valid: {len(discovered)} test files verified.", manifest


def classify_paths(paths: Iterable[str]) -> tuple[bool, bool, str]:
    """Classify a set of changed paths into execution decisions.

    Returns:
        (run_node, run_browser, reason)
    """
    clean_paths = [normalize_repo_path(p) for p in paths if p.strip()]
    if not clean_paths:
        return False, False, "No changed files detected"

    # Check for direct test runner or manifest changes
    triggers_test_infra: list[str] = []
    triggers_photo_editor: list[str] = []
    triggers_shared_frontend: list[str] = []
    triggers_browser_backend: list[str] = []
    triggers_native_node: list[str] = []
    safe_backend: list[str] = []
    docs_only: list[str] = []
    unknown_paths: list[str] = []

    for path in clean_paths:
        if any(pat.search(path) for pat in CI_TEST_INFRA_PATTERNS):
            triggers_test_infra.append(path)
        elif any(pat.search(path) for pat in PHOTO_EDITOR_PATTERNS):
            triggers_photo_editor.append(path)
        elif any(pat.search(path) for pat in SHARED_FRONTEND_PATTERNS):
            triggers_shared_frontend.append(path)
        elif any(pat.search(path) for pat in BROWSER_FACING_BACKEND_PATTERNS):
            triggers_browser_backend.append(path)
        elif any(pat.search(path) for pat in NATIVE_NODE_TEST_PATTERNS):
            triggers_native_node.append(path)
        elif any(pat.search(path) for pat in DOCS_PATTERNS):
            docs_only.append(path)
        elif any(pat.search(path) for pat in ISOLATED_BACKEND_PYTEST_PATTERNS):
            safe_backend.append(path)
        else:
            unknown_paths.append(path)

    # 1. Unknown or unclassified paths: conservative full execution
    if unknown_paths:
        return (
            True,
            True,
            f"Unknown or unclassified paths changed ({len(unknown_paths)}): {unknown_paths[:3]}",
        )

    req_node = False
    req_browser = False
    reasons: list[str] = []

    # 2. Test runner, workflow, or manifest changes: full execution
    if triggers_test_infra:
        req_node = True
        req_browser = True
        reasons.append(f"Test runner, workflow, or manifest changes ({len(triggers_test_infra)} files)")

    # 3. Photo Editor or gallery editor: full execution
    if triggers_photo_editor:
        req_node = True
        req_browser = True
        reasons.append(f"Photo Editor or gallery editor ({len(triggers_photo_editor)} files)")

    # 4. Shared frontend infrastructure: full execution
    if triggers_shared_frontend:
        req_node = True
        req_browser = True
        reasons.append(f"Shared frontend infrastructure changed ({len(triggers_shared_frontend)} files)")

    # 5. Browser-facing backend APIs, routes, or server lifecycle: full execution
    if triggers_browser_backend:
        req_node = True
        req_browser = True
        reasons.append(f"Authentication, sessions, or browser-facing backend API changed ({len(triggers_browser_backend)} files)")

    # 6. Native Node test files changed
    if triggers_native_node:
        req_node = True
        reasons.append(f"Native Node tests changed ({len(triggers_native_node)} files)")

    if req_node or req_browser:
        reason_str = "Selected suites triggered by: " + "; ".join(reasons)
        return req_node, req_browser, reason_str

    # 7. Documentation only
    if docs_only and not safe_backend:
        return (
            False,
            False,
            f"Documentation-only changes ({len(docs_only)} files; suites skipped)",
        )

    # 8. Safe backend Python / CLI without frontend, auth, or test-infra impact
    if safe_backend:
        return (
            False,
            False,
            f"Isolated backend Python / CLI changes ({len(safe_backend)} files; validated by pytest shards)",
        )

    # Fallback default
    return True, True, "Conservative fallback for unclassified change set"


def parse_decision_boolean(val: Any) -> bool | None:
    """Parse strict decision boolean.

    Accepted values:
    - Boolean True or string "true"
    - Boolean False or string "false"

    Rejects:
    - Empty strings
    - Missing values / None
    - Unexpected strings or numbers
    - Ambiguous or malformed representations
    """
    if isinstance(val, bool):
        return val
    if isinstance(val, str):
        normalized = val.strip().lower()
        if normalized == "true":
            return True
        if normalized == "false":
            return False
    return None


def evaluate_job_or_matrix_result(res_str: str) -> tuple[str, bool]:
    """Parse a job outcome or matrix result string into (canonical_status, is_all_equal).

    In GitHub Actions, `needs.<job_id>.result` for a matrix job can be either a
    single status string (e.g. 'success', 'failure', 'cancelled', 'skipped') or a
    JSON structure mapping matrix keys to individual job results.

    Returns:
    - (status, True) if all jobs/shards share the same status (e.g. ('success', True))
    - (first_non_success_status, False) if results are mixed (e.g. ('failure', False))
    """
    cleaned = (res_str or "").strip()
    if not cleaned:
        return "", False

    if cleaned in ("success", "failure", "cancelled", "skipped"):
        return cleaned, True

    try:
        val = json.loads(cleaned)
        if isinstance(val, str) and val in ("success", "failure", "cancelled", "skipped"):
            return val, True
        if isinstance(val, (dict, list)):
            items = list(val.values()) if isinstance(val, dict) else val
            if not items:
                return "", False
            # Check if all items match the first status
            first = items[0]
            if all(x == first for x in items):
                return str(first), True
            # Mixed: return failure or cancelled if present, else first
            for priority in ("failure", "cancelled", "skipped"):
                if priority in items:
                    return priority, False
            return str(first), False
    except Exception:
        pass

    return cleaned, False


def evaluate_gate_compliance(
    decision_result: str,
    run_node: str | bool,
    run_browser: str | bool,
    node_result: str,
    browser_result: str,
) -> tuple[bool, str]:
    """Evaluate aggregate gate compliance given job results and selection policy."""
    if decision_result != "success":
        return False, f"Coverage decision job did not succeed (status: {decision_result})"

    req_node = parse_decision_boolean(run_node)
    if req_node is None:
        return False, f"Invalid or missing Native Node decision: {run_node!r}"

    req_browser = parse_decision_boolean(run_browser)
    if req_browser is None:
        return False, f"Invalid or missing Chromium browser decision: {run_browser!r}"

    node_status, node_uniform = evaluate_job_or_matrix_result(node_result)
    browser_status, browser_uniform = evaluate_job_or_matrix_result(browser_result)

    if req_node:
        if node_status != "success" or not node_uniform:
            return False, f"Required Native Node suite did not succeed (status: {node_result})"
    else:
        if node_status != "skipped" or not node_uniform:
            return False, f"Native Node suite had unexpected status for permitted skip (status: {node_result})"

    if req_browser:
        if browser_status != "success" or not browser_uniform:
            return False, f"Required Chromium browser suite did not succeed (status: {browser_result})"
    else:
        if browser_status != "skipped" or not browser_uniform:
            return False, f"Chromium browser suite had unexpected status for permitted skip (status: {browser_result})"

    return True, "Authoritative native gate PASSED: all required suites succeeded; permitted skips verified."


def get_pr_diff_paths(base_sha: str, head_sha: str, repo_root: Path = ROOT) -> list[str]:
    """Get changed paths using git diff --name-status against merge base."""
    merge_base = subprocess.check_output(
        ["git", "merge-base", base_sha, head_sha],
        cwd=repo_root,
        stderr=subprocess.DEVNULL,
        text=True,
    ).strip()
    raw = subprocess.check_output(
        ["git", "diff", "--name-status", "-M", merge_base, head_sha],
        cwd=repo_root,
        text=True,
    )
    return parse_diff_paths(raw)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", help="PR base commit SHA or ref")
    parser.add_argument("--head", help="PR head commit SHA or ref")
    parser.add_argument("--files", nargs="*", help="Explicit changed files to evaluate")
    parser.add_argument("--full", action="store_true", help="Force full execution (Level C)")
    parser.add_argument("--validate-manifest", action="store_true", help="Only validate test manifest")
    parser.add_argument("--evaluate-gate", action="store_true", help="Evaluate aggregate gate compliance")
    parser.add_argument("--decision-result", default="", help="Coverage decision job result")
    parser.add_argument("--run-node", default="", help="Policy decision for run_node")
    parser.add_argument("--run-browser", default="", help="Policy decision for run_browser")
    parser.add_argument("--node-result", default="", help="Native node job result")
    parser.add_argument("--browser-result", default="", help="Browser job result")
    parser.add_argument("--github-output", help="Path to GITHUB_OUTPUT file")
    parser.add_argument("--step-summary", help="Path to GITHUB_STEP_SUMMARY file")
    args = parser.parse_args(argv)

    if args.evaluate_gate:
        ok, msg = evaluate_gate_compliance(
            decision_result=args.decision_result,
            run_node=args.run_node,
            run_browser=args.run_browser,
            node_result=args.node_result,
            browser_result=args.browser_result,
        )
        if ok:
            print(msg)
            return 0
        else:
            print(f"::error::{msg}", file=sys.stderr)
            return 1

    # 1. Always validate test manifest
    is_valid, msg, manifest = validate_test_manifest(MANIFEST_PATH, ROOT)
    if not is_valid:
        print(f"::error::{msg}", file=sys.stderr)
        return 1

    if args.validate_manifest and not (args.files or args.base or args.full):
        print(msg)
        return 0

    # 2. Determine execution level and selection decision
    if args.full:
        run_node = True
        run_browser = True
        reason = "Full integration validation triggered (Level C)"
        paths = []
    elif args.files is not None:
        paths = args.files
        run_node, run_browser, reason = classify_paths(paths)
    elif args.base and args.head:
        try:
            paths = get_pr_diff_paths(args.base, args.head, ROOT)
            run_node, run_browser, reason = classify_paths(paths)
        except Exception as exc:
            run_node = True
            run_browser = True
            reason = f"Failed to compute PR diff ({exc}); falling back to full validation fail-closed"
            paths = []
    else:
        # Default fallback (e.g. push or workflow_dispatch)
        run_node = True
        run_browser = True
        reason = "Full integration validation (Level C default)"
        paths = []

    result = {
        "run_node": run_node,
        "run_browser": run_browser,
        "reason": reason,
        "inventory_verified": True,
        "total_test_files": sum(
            len(manifest[k]) if isinstance(manifest[k], list) else len(manifest[k].keys())
            for k in manifest
        ),
        "changed_files_count": len(paths),
    }

    # Print JSON output
    print(json.dumps(result, indent=2))

    # Write GitHub Actions outputs if configured
    gh_output_path = args.github_output or os.environ.get("GITHUB_OUTPUT")
    if gh_output_path:
        with open(gh_output_path, "a", encoding="utf-8") as f:
            f.write(f"run_node={'true' if run_node else 'false'}\n")
            f.write(f"run_browser={'true' if run_browser else 'false'}\n")
            f.write(f"decision_reason={reason}\n")
            f.write(f"total_test_files={result['total_test_files']}\n")

    # Write GitHub Actions step summary if configured
    summary_path = args.step_summary or os.environ.get("GITHUB_STEP_SUMMARY")
    if summary_path:
        with open(summary_path, "a", encoding="utf-8") as f:
            f.write("## Selective Test Coverage Decision\n\n")
            f.write(f"- **Manifest Verification:** PASSED ({result['total_test_files']} native test files)\n")
            f.write(f"- **Policy Decision:** `run_node={run_node}`, `run_browser={run_browser}`\n")
            f.write(f"- **Reason:** {reason}\n")
            if paths:
                f.write(f"- **Evaluated Files ({len(paths)}):**\n")
                for p in paths[:20]:
                    f.write(f"  - `{p}`\n")
                if len(paths) > 20:
                    f.write(f"  - *...and {len(paths) - 20} more*\n")

    return 0


if __name__ == "__main__":
    sys.exit(main())
