#!/usr/bin/env python3
"""Run real application Playwright validation against an isolated native Odysseus server."""
import contextlib
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

# Real-application browser scripts. Each one drives the running application and its
# real backend; none stubs HTTP. The first one also writes the structured results the
# report is generated from.
MAIN_SUITE = "tests/browser/test_real_application_wysiwyg.mjs"
REGRESSION_SUITES = (
    "tests/browser/test_wysiwyg_preview_parity.mjs",
    "tests/browser/test_wysiwyg_loose_lists.mjs",
    "tests/browser/test_wysiwyg_feature_coverage.mjs",
    "tests/browser/test_wysiwyg_compat_real_app.mjs",
    "tests/browser/test_markdown_draft_reload.mjs",
    "tests/browser/test_wysiwyg_controls.mjs",
    "tests/browser/test_wysiwyg_geometry.mjs",
    "tests/browser/test_markdown_selection_context.mjs",
)
# Needs real accounts, so it runs against a second server with authentication enabled.
AUTH_SUITES = ("tests/browser/test_logout_draft_isolation.mjs",)


def artifacts_dir() -> Path:
    """Where reports are written. Resolved lazily so importing this module touches nothing."""
    return Path(os.environ.get("ODYSSEUS_VALIDATION_DIR") or (ROOT / "artifacts" / "validation"))


def get_git_revision() -> str:
    try:
        res = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True)
        return res.stdout.strip()
    except Exception:
        return "unknown"


@contextlib.contextmanager
def isolated_server(auth_enabled: bool):
    """Run the application on loopback with its own database, data directory and ports.

    Nothing here may reach a developer's or a production service: the database and
    data directory are disposable, the vector store port is reserved but never
    listened on, and a local `.env` is not read.
    """
    with tempfile.TemporaryDirectory(prefix="ody-val-") as tmpdir, socket.socket() as chroma_guard:
        tmp_path = Path(tmpdir)
        data_dir = tmp_path / "data"
        fastembed_dir = tmp_path / "fastembed"
        data_dir.mkdir(parents=True, exist_ok=True)
        fastembed_dir.mkdir(parents=True, exist_ok=True)

        # Reserve a non-listening port so initialization cannot reach a real vector store.
        chroma_guard.bind(("127.0.0.1", 0))
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        env = dict(os.environ)
        env.update(
            DATABASE_URL=f"sqlite:///{tmp_path / 'doc_validation.db'}",
            ODYSSEUS_DATA_DIR=str(data_dir),
            AUTH_ENABLED="true" if auth_enabled else "false",
            LOCALHOST_BYPASS="false",
            SECURE_COOKIES="false",
            CHROMADB_HOST="127.0.0.1",
            CHROMADB_PORT=str(chroma_guard.getsockname()[1]),
            CHROMADB_CONNECT_TIMEOUT="0.05",
            ODYSSEUS_DISABLE_MCP="1",
            ODYSSEUS_STARTUP_WARMUPS="0",
            ODYSSEUS_TOOL_INDEX_PREWARM="0",
            FASTEMBED_CACHE_PATH=str(fastembed_dir),
            PYTHON_DOTENV_DISABLED="1",
        )
        env.pop("PYTHONPATH", None)

        print(f"Launching isolated Odysseus uvicorn backend (auth {'on' if auth_enabled else 'off'}) on 127.0.0.1:{port}...")
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
                    raise RuntimeError(f"Server exited prematurely:\n{uvicorn_log_path.read_text(encoding='utf-8')}")
                try:
                    with urlopen(f"{origin}/", timeout=1) as res:
                        if res.status == 200:
                            print("Server ready!")
                            break
                except Exception:
                    time.sleep(0.3)
            else:
                raise RuntimeError("Server did not become ready within 45s")
            yield origin
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


def run_script(script: str, origin: str, output_dir: Path, timeout: int = 300) -> tuple[int, list[str]]:
    """Run one browser script against `origin`, streaming and returning its output."""
    env = dict(os.environ)
    env.update(ODYSSEUS_VALIDATION_ORIGIN=origin, ODYSSEUS_VALIDATION_DIR=str(output_dir))
    print(f"Executing {script}...")
    proc = subprocess.Popen(
        ["node", script], cwd=ROOT, env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
    )
    lines: list[str] = []
    try:
        for line in iter(proc.stdout.readline, ""):
            print(line, end="", flush=True)
            lines.append(line)
        proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        print(f"\n[ERROR] {script} timed out after {timeout}s!", flush=True)
        lines.append(f"timed out after {timeout}s\n")
        return 1, lines
    finally:
        proc.stdout.close()
    return proc.returncode, lines


def main():
    rev = get_git_revision()
    print(f"Starting Real Application Playwright Validation on revision {rev}")
    output_dir = artifacts_dir()
    output_dir.mkdir(parents=True, exist_ok=True)
    print(f"Writing validation artifacts to {output_dir}")

    extra: list[dict] = []
    with isolated_server(auth_enabled=False) as origin:
        json_file = output_dir / "playwright-validation-results.json"
        json_file.unlink(missing_ok=True)
        retcode, _ = run_script(MAIN_SUITE, origin, output_dir)
        results = json.loads(json_file.read_text(encoding="utf-8")) if json_file.exists() else {}
        for script in REGRESSION_SUITES:
            code, _ = run_script(script, origin, output_dir)
            extra.append({"script": script, "auth": "disabled", "passed": code == 0})
            retcode = retcode or code

    with isolated_server(auth_enabled=True) as auth_origin:
        for script in AUTH_SUITES:
            code, _ = run_script(script, auth_origin, output_dir)
            extra.append({"script": script, "auth": "enabled", "passed": code == 0})
            retcode = retcode or code

    generate_validation_report(rev, origin, results, retcode == 0, extra, output_dir)
    return retcode


def generate_validation_report(revision: str, origin: str, results: dict, success: bool, extra: list, output_dir: Path):
    report_file = output_dir / "PLAYWRIGHT_VALIDATION.md"
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
        "- **Auth mode:** Disabled (`AUTH_ENABLED=false`) for the main suite and the regression scripts. The logout suite runs against a second isolated server with `AUTH_ENABLED=true` and real accounts.",
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

    lines.extend(["", "## Additional real-application regressions", "", "| Script | Auth mode | Status |", "|---|---|---|"])
    for entry in extra:
        lines.append(f"| `{entry['script']}` | {entry['auth']} | **{'PASSED' if entry['passed'] else 'FAILED'}** |")

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
        f"- Observed HTTP 401 responses: {len(results.get('unauthorizedResponses', []))}.",
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
