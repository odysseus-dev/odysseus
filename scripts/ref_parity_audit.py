#!/usr/bin/env python3
"""Read-only audit of what one git ref carries that another does not.

Two long-lived lines that are not merged into each other drift silently. A fix
landed on one of them leaves no mark on the other, and nothing in git tells you
so: the two histories share only a distant merge base, so `git log A..B` lists
thousands of commits whose content is in fact already present on both sides
under different SHAs.

This script answers the question that actually matters at release time -- which
commits on the source ref left *no trace at all* in the target ref -- by
sampling distinctive added lines from each commit and searching the target tree
for them. It also reports the file-level presence diff, which catches the case
the line sampling cannot: a fix whose production change was reproduced on the
target but whose test file was never brought over.

It is read-only. It runs `git log`, `git show`, `git diff`, `git grep`,
`git ls-tree` and `git merge-base`, writes nothing to the repository, touches no
remote, and does not import the Odysseus application package.

Usage:

    scripts/ref_parity_audit.py --source public/dev --target lab --since 2026-08-10

Read `docs/ref-parity-audit.md` before acting on the output: the line sampling
is a heuristic and the report labels which of its verdicts are exact.
"""
import argparse
import fnmatch
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]

# Paths whose contents are never worth probing: vendored third-party code,
# committed build output, lockfiles and binaries. A distinctive line does not
# exist in a minified bundle, and a lockfile churns on every dependency bump.
DEFAULT_EXCLUDES = (
    "static/lib/*",
    "static/js/editor/build/*",
    "*.min.js",
    "*.min.css",
    "*.map",
    "package-lock.json",
    "*.lock",
    "*.png",
    "*.jpg",
    "*.jpeg",
    "*.gif",
    "*.ico",
    "*.webp",
    "*.svg",
    "*.pdf",
    "*.woff",
    "*.woff2",
    "*.ttf",
    "*.otf",
    "*.mp3",
    "*.mp4",
    "*.wav",
    "*.zip",
    "*.gz",
)

# A probe has to be long enough and carry enough named things to be unlikely to
# appear by coincidence. `return hosts` is in a hundred files; a line naming two
# identifiers over 24 characters is usually unique to the change that added it.
MIN_PROBE_LENGTH = 24
MIN_PROBE_IDENTIFIERS = 2
IDENTIFIER_RE = re.compile(r"[A-Za-z_][A-Za-z0-9_]{2,}")

FIELD_SEP = "\x1f"

VERDICT_ABSENT = "absent"
VERDICT_PARTIAL = "partial"
VERDICT_PRESENT = "present"
VERDICT_NO_PROBE = "no-probe"


class GitError(RuntimeError):
    """A git invocation failed in a way the audit cannot work around."""


@dataclass
class Commit:
    sha: str
    author: str
    date: str
    subject: str
    parent_count: int


@dataclass
class CommitVerdict:
    commit: Commit
    probes: tuple[str, ...]
    found: tuple[str, ...]
    paths: tuple[str, ...]

    @property
    def verdict(self) -> str:
        if not self.probes:
            return VERDICT_NO_PROBE
        if not self.found:
            return VERDICT_ABSENT
        if len(self.found) < len(self.probes):
            return VERDICT_PARTIAL
        return VERDICT_PRESENT


@dataclass
class Report:
    source: str
    source_sha: str
    target: str
    target_sha: str
    merge_base: str
    since: str | None
    until: str | None
    traversal: str
    probe_limit: int
    verdicts: list[CommitVerdict] = field(default_factory=list)
    source_only_files: tuple[str, ...] = ()
    target_only_files: tuple[str, ...] = ()

    def by_verdict(self, verdict: str) -> list[CommitVerdict]:
        return [v for v in self.verdicts if v.verdict == verdict]


# --------------------------------------------------------------------------- #
# git plumbing
# --------------------------------------------------------------------------- #


def run_git(args: Sequence[str], repo: Path) -> str:
    """Run a read-only git command and return stdout, raising on failure."""
    proc = subprocess.run(
        ["git", "-C", str(repo), *args],
        capture_output=True,
        text=True,
    )
    if proc.returncode != 0:
        raise GitError(f"git {' '.join(args)} failed: {proc.stderr.strip()}")
    return proc.stdout


def resolve_ref(ref: str, repo: Path) -> str:
    return run_git(["rev-parse", "--short=8", ref], repo).strip()


def merge_base(source: str, target: str, repo: Path) -> str:
    try:
        return run_git(["merge-base", source, target], repo).strip()[:8]
    except GitError:
        # Unrelated histories have no merge base. That is a finding, not a crash.
        return ""


def list_commits(
    source: str,
    target: str,
    repo: Path,
    since: str | None = None,
    until: str | None = None,
    traversal: str = "linear",
) -> list[Commit]:
    """List commits reachable from `source` but not from `target`.

    `linear` drops merge commits and reports the individual authored commits,
    which is what finds a fix that arrived on a side branch. `first-parent`
    reports one entry per merge into the source branch, which reads as one row
    per merged pull request.
    """
    args = [
        "log",
        "--date=short",
        f"--format=%H{FIELD_SEP}%an{FIELD_SEP}%cd{FIELD_SEP}%p{FIELD_SEP}%s",
    ]
    args.append("--no-merges" if traversal == "linear" else "--first-parent")
    if since:
        args.append(f"--since={since}")
    if until:
        args.append(f"--until={until}")
    args.append(f"{target}..{source}")

    commits = []
    for line in run_git(args, repo).splitlines():
        if not line.strip():
            continue
        sha, author, date, parents, subject = line.split(FIELD_SEP, 4)
        commits.append(
            Commit(
                sha=sha,
                author=author,
                date=date,
                subject=subject,
                parent_count=len(parents.split()) if parents.strip() else 0,
            )
        )
    return commits


def commit_diff(commit: Commit, repo: Path) -> str:
    """Return the commit's patch with no context lines.

    A merge is diffed against its first parent so the whole merged content is
    visible; `git show` would otherwise print only the conflicting hunks.
    """
    if commit.parent_count > 1:
        return run_git(
            ["diff", "--no-color", "--no-renames", "-U0", f"{commit.sha}^1", commit.sha],
            repo,
        )
    return run_git(
        ["show", "--no-color", "--no-renames", "-U0", "--format=", commit.sha], repo
    )


def probe_present(probe: str, ref: str, repo: Path) -> bool:
    """Is this exact text anywhere in the ref's tree?

    The whole tree is searched on purpose. The question is whether the change
    left a trace at all, not whether it landed in the same file -- a ported fix
    routinely moves, and the exclusion list only governs where probes come
    from.
    """
    proc = subprocess.run(
        ["git", "-C", str(repo), "grep", "--fixed-strings", "--quiet", "-e", probe, ref],
        capture_output=True,
        text=True,
    )
    if proc.returncode not in (0, 1):
        raise GitError(f"git grep failed for {ref}: {proc.stderr.strip()}")
    return proc.returncode == 0


def list_tree(ref: str, repo: Path) -> list[str]:
    raw = run_git(["ls-tree", "-r", "-z", "--name-only", ref], repo)
    return [path for path in raw.split("\0") if path]


# --------------------------------------------------------------------------- #
# probe selection (pure)
# --------------------------------------------------------------------------- #


def is_excluded(path: str, patterns: Iterable[str]) -> bool:
    name = path.rsplit("/", 1)[-1]
    return any(
        fnmatch.fnmatch(path, pattern) or fnmatch.fnmatch(name, pattern)
        for pattern in patterns
    )


def added_lines(patch: str, excludes: Iterable[str]) -> list[tuple[str, str]]:
    """Extract `(path, added line)` pairs from a unified diff."""
    results = []
    path = None
    skip = False
    for line in patch.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            path = None if target == "/dev/null" else target[2:] if target.startswith("b/") else target
            skip = path is None or is_excluded(path, excludes)
        elif line.startswith("--- ") or line.startswith("diff --git "):
            continue
        elif line.startswith("+") and path and not skip:
            results.append((path, line[1:]))
    return results


def probe_score(text: str) -> int:
    """Rank a candidate probe: distinct named things first, then length."""
    identifiers = set(IDENTIFIER_RE.findall(text))
    return len(identifiers) * 1000 + min(len(text), 400)


def is_probe_candidate(text: str) -> bool:
    stripped = text.strip()
    if len(stripped) < MIN_PROBE_LENGTH:
        return False
    if "\0" in stripped:
        return False
    return len(set(IDENTIFIER_RE.findall(stripped))) >= MIN_PROBE_IDENTIFIERS


def pick_probes(lines: Sequence[tuple[str, str]], limit: int) -> list[str]:
    """Pick up to `limit` distinctive stripped lines, highest-scoring first.

    Leading and trailing whitespace is dropped so a re-indented port still
    counts as present. Ties break on first appearance, keeping the output
    stable across runs.
    """
    seen: dict[str, int] = {}
    for index, (_path, text) in enumerate(lines):
        stripped = text.strip()
        if not is_probe_candidate(stripped) or stripped in seen:
            continue
        seen[stripped] = index
    ranked = sorted(seen, key=lambda text: (-probe_score(text), seen[text]))
    return ranked[:limit]


# --------------------------------------------------------------------------- #
# audit
# --------------------------------------------------------------------------- #


def audit(
    source: str,
    target: str,
    repo: Path,
    since: str | None = None,
    until: str | None = None,
    traversal: str = "linear",
    probe_limit: int = 4,
    excludes: Sequence[str] = DEFAULT_EXCLUDES,
    progress: bool = False,
) -> Report:
    report = Report(
        source=source,
        source_sha=resolve_ref(source, repo),
        target=target,
        target_sha=resolve_ref(target, repo),
        merge_base=merge_base(source, target, repo),
        since=since,
        until=until,
        traversal=traversal,
        probe_limit=probe_limit,
    )

    commits = list_commits(source, target, repo, since, until, traversal)
    for index, commit in enumerate(commits, start=1):
        if progress:
            print(
                f"\r[{index}/{len(commits)}] {commit.sha[:8]}",
                end="",
                file=sys.stderr,
                flush=True,
            )
        lines = added_lines(commit_diff(commit, repo), excludes)
        probes = pick_probes(lines, probe_limit)
        found = tuple(p for p in probes if probe_present(p, target, repo))
        report.verdicts.append(
            CommitVerdict(
                commit=commit,
                probes=tuple(probes),
                found=found,
                paths=tuple(dict.fromkeys(path for path, _ in lines)),
            )
        )
    if progress:
        print("", file=sys.stderr)

    source_files = {p for p in list_tree(source, repo) if not is_excluded(p, excludes)}
    target_files = {p for p in list_tree(target, repo) if not is_excluded(p, excludes)}
    report.source_only_files = tuple(sorted(source_files - target_files))
    report.target_only_files = tuple(sorted(target_files - source_files))
    return report


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #


def _commit_table(verdicts: Sequence[CommitVerdict]) -> list[str]:
    rows = [
        "| Commit | Committed | Author | Probes found | Subject |",
        "|---|---|---|---|---|",
    ]
    for item in verdicts:
        rows.append(
            f"| `{item.commit.sha[:8]}` | {item.commit.date} | {item.commit.author} "
            f"| {len(item.found)}/{len(item.probes)} | {item.commit.subject} |"
        )
    return rows


def _file_list(paths: Sequence[str], top: int) -> list[str]:
    lines = [f"- `{path}`" for path in paths[:top]]
    if len(paths) > top:
        lines.append(f"- … and {len(paths) - top} more")
    return lines


def render_markdown(report: Report, top: int = 50) -> str:
    absent = report.by_verdict(VERDICT_ABSENT)
    partial = report.by_verdict(VERDICT_PARTIAL)
    present = report.by_verdict(VERDICT_PRESENT)
    no_probe = report.by_verdict(VERDICT_NO_PROBE)

    window = []
    if report.since:
        window.append(f"since {report.since}")
    if report.until:
        window.append(f"until {report.until}")

    out = [
        "# Ref parity audit",
        "",
        f"Source `{report.source}` @ `{report.source_sha}` → "
        f"target `{report.target}` @ `{report.target_sha}`.",
        f"Merge base `{report.merge_base or 'none (unrelated histories)'}`.",
        f"{len(report.verdicts)} commits on the source and not the target "
        f"({report.traversal} traversal"
        + (", " + ", ".join(window) if window else "")
        + f"), up to {report.probe_limit} probes each.",
        "",
        f"No trace in the target: **{len(absent)}**. "
        f"Partly present: **{len(partial)}**. "
        f"Fully present: **{len(present)}**. "
        f"Unprobeable: **{len(no_probe)}**.",
        "",
        "## Commits with no trace in the target",
        "",
    ]
    out += _commit_table(absent) if absent else ["None."]

    out += [
        "",
        "## Commits only partly present",
        "",
        "A partial verdict is inconclusive, not a finding: a line can move or be "
        "rewritten by a refactor on the target and still be the same change. Read the "
        "diff before porting anything from this table.",
        "",
    ]
    out += _commit_table(partial) if partial else ["None."]

    out += ["", "## Commits with no usable probe", ""]
    if no_probe:
        out += [
            "Deletion-only commits, and commits touching nothing but excluded paths. "
            "The audit has no verdict on these.",
            "",
        ] + _commit_table(no_probe)
    else:
        out.append("None.")

    out += [
        "",
        f"## Files on the source and not the target ({len(report.source_only_files)})",
        "",
        "Exact, not sampled. A file here whose commit is reported fully present is "
        "usually a fix that was reproduced without its test.",
        "",
    ]
    out += _file_list(report.source_only_files, top) if report.source_only_files else ["None."]

    out += [
        "",
        f"## Files on the target and not the source ({len(report.target_only_files)})",
        "",
    ]
    out += _file_list(report.target_only_files, top) if report.target_only_files else ["None."]
    out.append("")
    return "\n".join(out)


def render_json(report: Report) -> str:
    return json.dumps(
        {
            "source": {"ref": report.source, "sha": report.source_sha},
            "target": {"ref": report.target, "sha": report.target_sha},
            "merge_base": report.merge_base,
            "since": report.since,
            "until": report.until,
            "traversal": report.traversal,
            "probe_limit": report.probe_limit,
            "totals": {
                verdict: len(report.by_verdict(verdict))
                for verdict in (
                    VERDICT_ABSENT,
                    VERDICT_PARTIAL,
                    VERDICT_PRESENT,
                    VERDICT_NO_PROBE,
                )
            },
            "commits": [
                {
                    "sha": item.commit.sha,
                    "date": item.commit.date,
                    "author": item.commit.author,
                    "subject": item.commit.subject,
                    "verdict": item.verdict,
                    "probes": list(item.probes),
                    "probes_found": list(item.found),
                    "paths": list(item.paths),
                }
                for item in report.verdicts
            ],
            "source_only_files": list(report.source_only_files),
            "target_only_files": list(report.target_only_files),
        },
        indent=2,
        sort_keys=True,
    )


# --------------------------------------------------------------------------- #
# cli
# --------------------------------------------------------------------------- #


def positive_int(value: str) -> int:
    parsed = int(value)
    if parsed < 1:
        raise argparse.ArgumentTypeError("must be 1 or greater")
    return parsed


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Read-only audit of which commits on one ref left no trace in another."
    )
    parser.add_argument("--source", required=True, help="Ref whose commits are audited")
    parser.add_argument("--target", required=True, help="Ref searched for traces of them")
    parser.add_argument("--repo", default=str(REPO_ROOT), help="Repository to run in")
    parser.add_argument("--since", help="Only commits committed on or after this date")
    parser.add_argument("--until", help="Only commits committed on or before this date")
    parser.add_argument(
        "--traversal",
        choices=["linear", "first-parent"],
        default="linear",
        help="linear: individual commits, no merges. first-parent: one row per merge",
    )
    parser.add_argument(
        "--probes", type=positive_int, default=4, help="Probe lines sampled per commit"
    )
    parser.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="Extra path glob whose lines are not used as probes (repeatable)",
    )
    parser.add_argument(
        "--no-default-excludes",
        action="store_true",
        help="Drop the built-in vendored/lockfile/binary exclusions",
    )
    parser.add_argument("--format", choices=["markdown", "json"], default="markdown")
    parser.add_argument("--top", type=positive_int, default=50, help="Rows per file list")
    parser.add_argument("--output", help="Write the report here instead of stdout")
    parser.add_argument("--quiet", action="store_true", help="No progress output")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    excludes = list(args.exclude)
    if not args.no_default_excludes:
        excludes = list(DEFAULT_EXCLUDES) + excludes

    try:
        report = audit(
            source=args.source,
            target=args.target,
            repo=Path(args.repo),
            since=args.since,
            until=args.until,
            traversal=args.traversal,
            probe_limit=args.probes,
            excludes=excludes,
            progress=not args.quiet and sys.stderr.isatty(),
        )
    except GitError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    text = render_json(report) if args.format == "json" else render_markdown(report, args.top)
    if args.output:
        Path(args.output).write_text(text + "\n", encoding="utf-8")
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
