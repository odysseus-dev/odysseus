#!/usr/bin/env python3
"""Vault conformance heartbeat writer (obsidian-PhD contract, vault PR #351).

Writes one dated JSON artifact per scheduled cycle into the vault checkout at
``automation/review/routine-reports/odysseus-heartbeat/<date>.heartbeat.json``
attesting which action allowlist this Odysseus deployment loaded and what it
last dispatched. The vault-side validator (``Scripts/automation/
odysseus_heartbeat.py``) enforces the schema fail-closed; this writer is its
mirror and must stay field-compatible.

Boundaries (vault contract):
- Writes ONLY under the vault's review-side staging path; never canonical
  vault paths. Staging to Git is owned by the vault's remote-upkeep branch
  runner, never by this script.
- No secrets in the payload: last_dispatch carries task name, status, and
  timestamps only — never prompts, results, or tokens.
- Fail-closed: if the allowlist cannot be read and hashed, exit non-zero
  rather than emit an unverifiable heartbeat.

Run from the Odysseus repo root with its venv python (same convention as the
installer scripts), typically inside the vault remote-upkeep cycle:

    venv/bin/python scripts/vault_heartbeat.py --vault-root ~/projects/vault-runtime
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

HEARTBEAT_SCHEMA_VERSION = 1
HEARTBEAT_REPORT_KIND = "odysseus-heartbeat"
ACTIONS_YAML_RELATIVE_PATH = "automation/config/odysseus_actions.yaml"
HEARTBEAT_RELATIVE_DIR = "automation/review/routine-reports/odysseus-heartbeat"

# Bounded parser contract shared with the vault validator
# (Scripts/automation/odysseus_heartbeat.py): two-level actions block,
# enabled defaults to false.
_ACTION_NAME_RE = re.compile(r"^  ([A-Za-z0-9][A-Za-z0-9_-]*):\s*(#.*)?$")
_ENABLED_RE = re.compile(r"^    enabled:\s*(true|false)\b")
_TOP_LEVEL_KEY_RE = re.compile(r"^[A-Za-z_]")


def actions_yaml_sha256(path: Path) -> str:
    """SHA-256 over UTF-8 bytes with line endings normalised to LF.

    Mirrors the vault validator so CRLF checkouts and this Linux service
    hash identical content.
    """
    text = path.read_text(encoding="utf-8").replace("\r\n", "\n").replace("\r", "\n")
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def parse_allowlisted_actions(text: str) -> dict[str, bool]:
    actions: dict[str, bool] = {}
    in_actions = False
    current: str | None = None
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if not line or line.lstrip().startswith("#"):
            continue
        if _TOP_LEVEL_KEY_RE.match(line):
            in_actions = line.startswith("actions:")
            current = None
            continue
        if not in_actions:
            continue
        name_match = _ACTION_NAME_RE.match(line)
        if name_match:
            current = name_match.group(1)
            actions[current] = False
            continue
        if current is not None:
            enabled_match = _ENABLED_RE.match(line)
            if enabled_match:
                actions[current] = enabled_match.group(1) == "true"
    return actions


def service_version() -> str:
    try:
        from core.constants import APP_VERSION  # type: ignore

        return str(APP_VERSION)
    except Exception:
        return "unknown"


def last_dispatch_summary() -> dict:
    """Most recent finished scheduler run: names and timestamps only.

    Never includes prompts, results, errors, or token counts — those may
    carry content that must not land in the vault repo.
    """
    try:
        from core.database import ScheduledTask, SessionLocal, TaskRun  # type: ignore
    except Exception as exc:  # pragma: no cover - import environment dependent
        return {"status": "unavailable", "reason": f"scheduler db import failed: {exc}"}
    try:
        db = SessionLocal()
    except Exception as exc:  # pragma: no cover
        return {"status": "unavailable", "reason": f"scheduler db session failed: {exc}"}
    try:
        run = (
            db.query(TaskRun)
            .filter(TaskRun.finished_at.isnot(None))
            .order_by(TaskRun.finished_at.desc())
            .first()
        )
        if run is None:
            return {"status": "none", "reason": "no finished task runs recorded"}
        task = db.query(ScheduledTask).filter(ScheduledTask.id == run.task_id).first()
        return {
            "status": str(run.status or "unknown"),
            "task_name": str(task.name) if task is not None else "unknown",
            "started_at": run.started_at.replace(tzinfo=timezone.utc).isoformat()
            if run.started_at
            else None,
            "finished_at": run.finished_at.replace(tzinfo=timezone.utc).isoformat()
            if run.finished_at
            else None,
        }
    except Exception as exc:
        return {"status": "unavailable", "reason": f"scheduler db query failed: {exc}"}
    finally:
        try:
            db.close()
        except Exception:
            pass


def build_payload(vault_root: Path) -> dict:
    allowlist_path = vault_root / ACTIONS_YAML_RELATIVE_PATH
    if not allowlist_path.is_file():
        raise SystemExit(
            f"Fail-closed: committed allowlist not found: {allowlist_path}"
        )
    digest = actions_yaml_sha256(allowlist_path)
    actions = parse_allowlisted_actions(
        allowlist_path.read_text(encoding="utf-8")
    )
    enabled = sorted(name for name, flag in actions.items() if flag)
    return {
        "schema_version": HEARTBEAT_SCHEMA_VERSION,
        "report_kind": HEARTBEAT_REPORT_KIND,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "service_version": service_version(),
        "actions_yaml_sha256": digest,
        "enabled_actions": enabled,
        "last_dispatch": last_dispatch_summary(),
    }


def write_heartbeat(vault_root: Path, payload: dict) -> Path:
    directory = vault_root / HEARTBEAT_RELATIVE_DIR
    directory.mkdir(parents=True, exist_ok=True)
    date_stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    target = directory / f"{date_stamp}.heartbeat.json"
    # Atomic replace so a crashed run never leaves a truncated JSON for the
    # fail-closed validator to trip on.
    fd, tmp_name = tempfile.mkstemp(
        prefix=".heartbeat-", suffix=".tmp", dir=str(directory)
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True)
            handle.write("\n")
        os.replace(tmp_name, target)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise
    return target


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--vault-root",
        default=os.environ.get(
            "VAULT_RUNTIME_ROOT", str(Path.home() / "projects" / "vault-runtime")
        ),
        help="Vault checkout the heartbeat attests (default: ~/projects/vault-runtime "
        "or $VAULT_RUNTIME_ROOT).",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print the payload without writing the artifact.",
    )
    args = parser.parse_args(argv)

    vault_root = Path(args.vault_root).expanduser().resolve()
    if not vault_root.is_dir():
        print(f"Fail-closed: vault root not found: {vault_root}", file=sys.stderr)
        return 2

    payload = build_payload(vault_root)
    if args.dry_run:
        json.dump(payload, sys.stdout, indent=2, sort_keys=True)
        print()
        return 0
    target = write_heartbeat(vault_root, payload)
    print(f"Wrote {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
