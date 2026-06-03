"""scripts/_lib/cli.py — shared scaffolding for the `odysseus-*` CLIs.

Each top-level CLI imports a few helpers from here so they don't
have to redefine the same `_quiet_logs` / `_emit` / `_fail` /
parents-parser pattern. Usage:

    from scripts._lib.cli import quiet_logs, emit, fail, common_parser, run

    quiet_logs()
    try:
        from core.database import SessionLocal, Note  # or whatever
        quiet_logs()
    except ModuleNotFoundError as e:
        fail(f"{e}\\nhint: run from repo root with venv active.", code=2)

    def cmd_list(args):
        ...

    def build_parser():
        p = common_parser("odysseus-foo", "Description.")
        sub = p.add_subparsers(dest="cmd", required=True)
        pl = sub.add_parser("list", parents=[p._common_parents[0]])
        pl.set_defaults(func=cmd_list)
        return p

    if __name__ == "__main__":
        sys.exit(run(build_parser()))

The `--pretty` flag, repo-root-on-sys.path, and clean exit on
KeyboardInterrupt / unexpected exception are all handled centrally.
"""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path

# Make repo root importable. Tools are invoked as `scripts/odysseus-foo`
# from any cwd; we want `from core.database import ...` to work.
REPO_ROOT = Path(__file__).resolve().parent.parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


def quiet_logs() -> None:
    """Force the root logger down to WARNING (overridable via
    LOG_LEVEL=...). Call once before importing app modules and again
    *after* — some submodules call `logging.basicConfig` during their
    own import and re-raise the level to INFO."""
    level_name = os.environ.get("LOG_LEVEL", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)
    root = logging.getLogger()
    root.setLevel(level)
    for handler in root.handlers:
        handler.setLevel(level)


def emit(obj, args) -> None:
    """Write JSON to stdout. Pretty-print if `--pretty` was passed or
    stdout is a TTY. Uses `default=str` so SQLAlchemy datetimes etc.
    serialize cleanly."""
    pretty = getattr(args, "pretty", False) or sys.stdout.isatty()
    json.dump(
        obj, sys.stdout,
        indent=2 if pretty else None,
        default=str,
        ensure_ascii=False,
    )
    sys.stdout.write("\n")


def fail(msg: str, code: int = 1) -> None:
    """Print an error to stderr and exit non-zero. Doesn't return."""
    sys.stderr.write(f"error: {msg}\n")
    sys.exit(code)


VERSION = "0.1.0"  # bumped centrally; every odysseus-* CLI reports this


def common_parser(prog: str, description: str = "") -> argparse.ArgumentParser:
    """Return a top-level parser with `--pretty` and `--version` already
    wired up, and a stashed `_common_parents` list each subcommand should
    reuse via `parents=[...]` so the same flag works before AND after
    the subcommand name."""
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--pretty", action="store_true",
                        help="Pretty-print JSON output")

    p = argparse.ArgumentParser(prog=prog, description=description, parents=[common])
    p.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    p._common_parents = [common]  # consumed by callers when building sub-parsers
    return p


def run(parser: argparse.ArgumentParser, argv=None) -> int:
    """Parse args, dispatch to `args.func(args)`, return an exit code.
    Catches KeyboardInterrupt (→ 130) and uncaught exceptions (→ 1)
    with a friendly stderr message.

    Intercepts `--version` / `-V` before argparse can complain about the
    missing required subcommand — `argparse.required=True` on the
    subparsers fires first otherwise."""
    raw_argv = sys.argv[1:] if argv is None else list(argv)
    if any(a in ("--version", "-V") for a in raw_argv):
        sys.stdout.write(f"{parser.prog} {VERSION}\n")
        return 0

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        sys.stderr.write("interrupted\n")
        return 130
    except SystemExit:
        raise
    except Exception as e:
        fail(str(e))
    return 0
