"""Pure helpers for shaping cookbook task output for the status response.

Kept dependency-free (no FastAPI / SQLAlchemy imports) so the behavior can be
unit-tested without standing up the whole app.
"""


def error_aware_output_tail(full_snapshot: str, status: str) -> str:
    """Return the trailing slice of a task log for the status response.

    Failed tasks return the last 50 lines so the "Copy last 50 lines" action
    surfaces the actual error context (stack traces, build output). Running and
    other non-error tasks keep the cheaper 12-line tail to limit the payload on
    the 10s polling interval.
    """
    if not full_snapshot:
        return ""
    tail_lines = 50 if status == "error" else 12
    return "\n".join(full_snapshot.splitlines()[-tail_lines:])
