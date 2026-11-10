"""The area registry and the result table for the release smoke suite.

Pure stdlib on purpose: this module is the one part of the suite that has
to be readable and testable without a running instance, because it is
what decides whether the suite's output is honest.

Two lists matter here and they are both deliberate:

``COVERED`` names every feature area the suite drives, and the test
module that drives it. A row appears in the table whether or not its
module ran, so an area cannot quietly vanish from the report by having
its file deleted or renamed - it shows up as ``NOT RUN`` instead.

``DECLARED_GAPS`` names the areas the suite does *not* cover, with the
reason. They are printed alongside the results rather than left out,
because a smoke report that lists only what it checked reads as
coverage of everything it does not mention.
"""
from __future__ import annotations

import textwrap
from dataclasses import dataclass

# Result labels. ASCII only - no Unicode status glyphs anywhere in the
# table (repo convention: no emoji in UI or code).
PASS = "PASS"
FAIL = "FAIL"
SKIP = "SKIP"
NOT_RUN = "NOT RUN"
NOT_COVERED = "NOT COVERED"

# Precedence when one area's module produces several outcomes: a single
# failure decides the row, then a skip, then pass.
_PRECEDENCE = (FAIL, SKIP, PASS)

# Table geometry. Wide enough for the longest gap reason to read as a
# sentence, narrow enough to survive a normal terminal.
TABLE_WIDTH = 100
MIN_DETAIL_WIDTH = 30


@dataclass(frozen=True)
class Area:
    """One advertised feature area and the module that exercises it."""

    key: str
    label: str
    module: str


@dataclass(frozen=True)
class Gap:
    """An area this suite does not cover, and why it does not."""

    label: str
    reason: str


# Order is the order the table prints in: the chat surface first, then
# the feature areas README.md advertises, then the setup surface.
COVERED = (
    Area("chat", "Chat", "test_chat_smoke.py"),
    Area("compare", "Compare", "test_compare_smoke.py"),
    Area("notes", "Notes", "test_notes_smoke.py"),
    Area("calendar", "Calendar", "test_calendar_smoke.py"),
    Area("tasks", "Tasks (scheduled)", "test_tasks_smoke.py"),
    Area("documents", "Documents (editor)", "test_documents_smoke.py"),
    Area("documents_rag", "Documents (RAG)", "test_documents_rag_smoke.py"),
    Area("email", "Email", "test_email_smoke.py"),
    Area("memory", "Memory", "test_memory_smoke.py"),
    Area("uploads", "Uploads", "test_uploads_smoke.py"),
    Area("cookbook", "Cookbook", "test_cookbook_smoke.py"),
    Area("settings", "Settings", "test_settings_smoke.py"),
)

DECLARED_GAPS = (
    Gap(
        "Deep Research",
        "needs live web egress; the crawler has no deterministic stub and adding "
        "one would be an application change",
    ),
    Gap(
        "Web Search",
        "needs a reachable SearXNG or an external provider, so the result is not "
        "reproducible from a clean checkout",
    ),
    Gap(
        "Email over IMAP/SMTP",
        "covered through the existing ODYSSEUS_EMAIL_FIXTURE path only; no local "
        "mail server, so real account sync and send are untested",
    ),
    Gap(
        "Cookbook download and serve",
        "needs tmux, a GPU runtime and a multi-GB model download; only hardware "
        "fit and state sync are checked",
    ),
    Gap(
        "Gallery and photo editor",
        "already the one area with Playwright specs under tests/e2e/photo-editor/",
    ),
    Gap(
        "Agent tool loop",
        "measured by the checkpoint benchmark, which is the safety net that does "
        "cover the agent runtime",
    ),
    Gap(
        "MCP servers",
        "the built-in servers are stdio subprocesses whose readiness is not part "
        "of the app's own readiness contract",
    ),
    Gap(
        "Rendering and layout",
        "pinned by the computed-style snapshot in "
        "tests/test_css_computed_style_snapshot.py",
    ),
)

_MODULE_TO_KEY = {area.module: area.key for area in COVERED}


def area_for_module(module_name: str) -> str | None:
    """Map a test module filename to its area key, or None."""
    return _MODULE_TO_KEY.get(module_name)


def resolve(outcomes: list[str]) -> str:
    """Collapse one module's outcomes into the row's single result."""
    if not outcomes:
        return NOT_RUN
    for label in _PRECEDENCE:
        if label in outcomes:
            return label
    return NOT_RUN


def render_table(results, *, header="", areas=COVERED, gaps=DECLARED_GAPS,
                 width=TABLE_WIDTH) -> str:
    """Render the per-area table.

    ``results`` maps an area key to a mapping with ``result`` and,
    optionally, ``checks`` and ``detail``. Unknown keys are ignored and
    missing keys render as ``NOT RUN`` - the registry, not the run,
    decides which rows exist.
    """
    rows = []
    for area in areas:
        entry = results.get(area.key) or {}
        result = entry.get("result") or NOT_RUN
        checks = entry.get("checks")
        detail = entry.get("detail") or ""
        if result == NOT_RUN and not detail:
            detail = "no test ran for this area"
        rows.append((area.label, result,
                     "" if checks is None else str(checks), detail))

    labels = [row[0] for row in rows] + [gap.label for gap in gaps] + ["AREA"]
    label_width = max(len(label) for label in labels)
    result_width = max([len(row[1]) for row in rows] + [len(NOT_COVERED), len("RESULT")])
    checks_width = max([len(row[2]) for row in rows] + [len("CHECKS")])
    # Indent + label + gap + result + gap + checks + gap, then the detail.
    detail_indent = 2 + label_width + 2 + result_width + 2 + checks_width + 2
    detail_width = max(width - detail_indent, MIN_DETAIL_WIDTH)

    def row_lines(label, result, checks, detail):
        first = (f"  {label.ljust(label_width)}  {result.ljust(result_width)}  "
                 f"{checks.rjust(checks_width)}  ")
        wrapped = textwrap.wrap(detail, detail_width) or [""]
        out = [(first + wrapped[0]).rstrip()]
        out += [(" " * detail_indent + line).rstrip() for line in wrapped[1:]]
        return out

    lines = []
    if header:
        lines.extend([header, ""])
    lines.append(
        f"  {'AREA'.ljust(label_width)}  {'RESULT'.ljust(result_width)}  "
        f"{'CHECKS'.rjust(checks_width)}  DETAIL"
    )
    for row in rows:
        lines.extend(row_lines(*row))

    if gaps:
        lines.extend(["", "  Not covered, deliberately:"])
        for gap in gaps:
            lines.extend(row_lines(gap.label, NOT_COVERED, "", gap.reason))

    failed = [row for row in rows if row[1] == FAIL]
    skipped = [row for row in rows if row[1] == SKIP]
    not_run = [row for row in rows if row[1] == NOT_RUN]
    passed = len(rows) - len(failed) - len(skipped) - len(not_run)
    lines.extend(["", (
        f"  {passed} pass, {len(failed)} fail, {len(skipped)} skip, "
        f"{len(not_run)} not run, {len(gaps)} declared gaps"
    )])
    return "\n".join(lines)
