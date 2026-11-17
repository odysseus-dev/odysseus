#!/usr/bin/env python3
"""Run real application Playwright validation against an isolated native Odysseus server."""
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
from urllib.request import urlopen

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

ARTIFACTS_DIR = Path(os.environ.get("ODYSSEUS_VALIDATION_DIR") or (ROOT / "artifacts" / "validation"))
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)


def get_git_revision() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return "unknown"


def main():
    rev = get_git_revision()
    print(f"Starting Real Application Playwright Validation on revision {rev}")

    with tempfile.TemporaryDirectory(prefix="ody-val-") as tmpdir:
        tmp_path = Path(tmpdir)
        db_file = tmp_path / "doc_validation.db"
        data_dir = tmp_path / "data"
        fastembed_dir = tmp_path / "fastembed"
        data_dir.mkdir(parents=True, exist_ok=True)
        fastembed_dir.mkdir(parents=True, exist_ok=True)

        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        env = dict(os.environ)
        env.update(
            DATABASE_URL=f"sqlite:///{db_file}",
            ODYSSEUS_DATA_DIR=str(data_dir),
            AUTH_ENABLED="false",
            ODYSSEUS_STARTUP_WARMUPS="0",
            ODYSSEUS_TOOL_INDEX_PREWARM="0",
            FASTEMBED_CACHE_PATH=str(fastembed_dir),
        )
        env.pop("PYTHONPATH", None)

        print(f"Launching isolated Odysseus uvicorn backend on 127.0.0.1:{port}...")
        uvicorn_log_path = tmp_path / "uvicorn-validation.log"
        uvicorn_log = open(uvicorn_log_path, "w", encoding="utf-8")
        server = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "app:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=ROOT,
            env=env,
            stdout=uvicorn_log,
            stderr=subprocess.STDOUT,
            text=True,
            start_new_session=(os.name != "nt"),
        )

        from src.process_lifecycle import ProcessIdentity, terminate_identities, bind_descendants, term_kill_steps, OWNED
        server_identity = ProcessIdentity.capture(server.pid)

        origin = f"http://127.0.0.1:{port}"
        try:
            print("Waiting for server ready...")
            deadline = time.monotonic() + 45
            while time.monotonic() < deadline:
                if server.poll() is not None:
                    uvicorn_log.flush()
                    logs = uvicorn_log_path.read_text(encoding="utf-8")
                    raise RuntimeError(f"Server exited prematurely:\n{logs}")
                try:
                    with urlopen(f"{origin}/", timeout=1) as res:
                        if res.status == 200:
                            print("Server ready!")
                            break
                except Exception:
                    time.sleep(0.3)
            else:
                raise RuntimeError("Server did not become ready within 45s")

            # Run Playwright validation suite
            test_env = dict(os.environ)
            test_env.update(
                ODYSSEUS_VALIDATION_ORIGIN=origin,
                ODYSSEUS_VALIDATION_DIR=str(ARTIFACTS_DIR),
            )
            json_file = ARTIFACTS_DIR / "playwright-validation-results.json"
            json_file.unlink(missing_ok=True)
            print("Executing Playwright test suite...")
            node_proc = subprocess.Popen(
                ["node", "tests/browser/test_real_application_wysiwyg.mjs"],
                cwd=ROOT,
                env=test_env,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
            )
            stdout_lines = []
            try:
                for line in iter(node_proc.stdout.readline, ''):
                    print(line, end="", flush=True)
                    stdout_lines.append(line)
                node_proc.wait(timeout=300)
            except subprocess.TimeoutExpired:
                node_proc.kill()
                print("\n[ERROR] Playwright suite timed out after 300s!", flush=True)
                raise
            finally:
                node_proc.stdout.close()

            retcode = node_proc.returncode

            # Load results JSON
            results = {}
            if json_file.exists():
                results = json.loads(json_file.read_text(encoding="utf-8"))

            # Generate PLAYWRIGHT_VALIDATION.md
            generate_validation_report(rev, origin, results, retcode == 0)

            return retcode
        finally:
            print("Cleaning up server processes...")
            try:
                uvicorn_log.close()
            except Exception:
                pass
            if server_identity.verdict() == OWNED:
                descendants = bind_descendants([server_identity.pid])
                terminate_identities([*(e.identity for e in reversed(descendants)), server_identity], steps=term_kill_steps(5))
            else:
                server.terminate()
                try:
                    server.wait(timeout=3)
                except Exception:
                    server.kill()
            print("Server stopped cleanly.")


def generate_validation_report(revision: str, origin: str, results: dict, success: bool):
    report_file = ARTIFACTS_DIR / "PLAYWRIGHT_VALIDATION.md"
    scenarios = results.get("scenarios", [])
    browser_smoke = results.get("browserSmoke", {})
    console_logs = results.get("consoleLogs", [])
    network_errors = results.get("networkErrors", [])
    chromium_version = results.get("chromiumVersion", "Unavailable")
    chromium_status = results.get("chromiumStatus", "NO RESULT")

    lines = [
        "# Odysseus WYSIWYG Document Editor: Real Application Playwright Validation",
        "",
        f"- **Verdict:** **{'PASSED' if success else 'FAILED'}**",
        f"- **HEAD at start of validation:** `{revision}` (tests used the working tree)",
        f"- **Validation Origin:** `{origin}`",
        "- **Test Database Isolation:** Ephemeral SQLite instance (`doc_validation.db`) in disposable temp directory",
        "- **Test Document Storage Isolation:** Isolated disposable directory (`ODYSSEUS_DATA_DIR`)",
        "- **Auth mode:** Disabled (`AUTH_ENABLED=false`). Authorization is covered separately by `tests/test_notification_log_auth.py`.",
        "",
        "## Browser Engine Coverage",
        "",
        "| Browser Engine | Family | Version | Scope | Status |",
        "|---|---|---|---|---|",
        f"| Chromium | Blink | {chromium_version} | Full 10-scenario end-to-end suite | **{chromium_status}** |",
        f"| Firefox | Gecko | {browser_smoke.get('firefox', {}).get('version', 'Unavailable')} | Visual admission smoke only | **{browser_smoke.get('firefox', {}).get('status', 'NO RESULT')}** |",
        f"| WebKit | WebKit (Safari engine) | {browser_smoke.get('webkit', {}).get('version', 'Unavailable')} | Visual admission smoke only | **{browser_smoke.get('webkit', {}).get('status', 'NO RESULT')}** |",
        "",
        "---",
        "",
        "## Scenario Results",
        "",
        "| # | Scenario | Status | Duration | Evidence & Behavior Verified |",
        "|---|---|---|---:|---|",
    ]

    for s in scenarios:
        sid = s.get("id")
        sname = s.get("name")
        stat = s.get("status")
        dur = f"{s.get('durationMs', 0)} ms"
        det = s.get("details", "")
        lines.append(f"| {sid} | {sname} | **{stat}** | {dur} | {det} |")

    if results.get("chromiumFailure"):
        lines.extend(["", "Chromium failure", "", results["chromiumFailure"]])

    lines.extend([
        "",
        "## Coverage limits",
        "",
        "- Firefox and WebKit open a seeded Markdown document and check that the Visual surface is visible.",
        "- Accessibility checks cover the textbox role, multiline state, save button label, polite live region attribute, and focus reception. Axe-core, WCAG conformance, and assistive technology were outside scope.",
        "- Clipboard coverage dispatches a synthetic paste event into the focused editor. The system clipboard was outside scope.",
        "- The suite does not benchmark editing. Earlier Chromium measurements showed increased latency for large or highly repetitive documents.",
        "- An edit that exceeds source reconciliation bounds or cannot round-trip safely can still block saving. Recovery uses tab-scoped session storage with bounded capacity. Closing a tab or browser is outside the recovery guarantee.",
        "",
        "## Diagnostics",
        "",
        f"- Failed browser requests: {len(network_errors)}",
        f"- Captured console and page diagnostics: {len(console_logs)}",
        f"- Observed HTTP 401 responses: {len(results.get('unauthorizedResponses', []))}. Scenario 6 deliberately returns a notification-log POST 401.",
    ])

    unauthorized = results.get("unauthorizedResponses", [])
    if unauthorized:
        lines.append("\n```json\n" + json.dumps(unauthorized, indent=2) + "\n```")

    lines.extend([
        "",
        "## Execution Commands",
        "",
        "```sh",
        "python scripts/run_real_playwright_validation.py",
        "```",
    ])

    report_file.write_text("\n".join(lines), encoding="utf-8")
    print(f"Report written to {report_file}")


if __name__ == "__main__":
    sys.exit(main())
