"""Agent-thread timeline dots must stay centred on the vertical rail.

Source-text assertions here are the narrow exception allowed by
TESTING_STANDARD.md: the invariant is pure CSS geometry, and driving it at
runtime would need a real layout engine, which the suite has no runner for.
The test parses the declared pixel values and recomputes the centres rather
than pinning literals, so retuning the spacing keeps it green — but moving a
breakpoint's padding without moving both dot offsets turns it red.

The thread's padding is restated once per breakpoint, and each breakpoint's
overrides follow its own `.agent-thread` rule, so a rule's position in the
file identifies the breakpoint it belongs to.
"""

import re
from pathlib import Path


CSS = (Path(__file__).resolve().parents[1] / "static" / "style.css").read_text(
    encoding="utf-8"
)

THREAD = r"^[ \t]*\.agent-thread[ \t]*\{"
RAIL = r"^[ \t]*\.agent-thread::before[ \t]*\{"
STEP_DOT = r"^[ \t]*\.agent-thread-dot[ \t]*\{"
END_DOT = (
    r"^[ \t]*\.agent-thread:not\(\.has-bottom\) "
    r"\.agent-thread-node\.open:last-child::after[ \t]*\{"
)


def _blocks(selector: str) -> list[tuple[int, str]]:
    """(position, declaration block) for each rule using this exact selector."""
    return [
        (m.start(), CSS[m.end() : CSS.index("}", m.end())])
        for m in re.finditer(selector, CSS, re.MULTILINE)
    ]


def _px(block: str, prop: str) -> float | None:
    m = re.search(rf"(?:^|;|/)\s*{prop}\s*:\s*(-?[\d.]+)px", block)
    return float(m.group(1)) if m else None


def _padding_left(block: str) -> float | None:
    explicit = _px(block, "padding-left")
    if explicit is not None:
        return explicit
    shorthand = re.search(r"(?:^|;)\s*padding\s*:([^;]+);", block)
    sides = shorthand.group(1).split() if shorthand else []
    return float(sides[3].removesuffix("px")) if len(sides) == 4 else None


def _breakpoints() -> list[tuple[float, int, int]]:
    """(padding-left, window start, window end) for each declared breakpoint."""
    threads = _blocks(THREAD)
    assert threads, "no .agent-thread rule found"
    bounds = [pos for pos, _ in threads] + [len(CSS)]
    return [
        (_padding_left(block), bounds[i], bounds[i + 1])
        for i, (_, block) in enumerate(threads)
    ]


def _in_force(selector: str, prop: str, start: int, end: int, fallback):
    """Value a breakpoint's window declares, else the one it inherits."""
    declared = [_px(b, prop) for pos, b in _blocks(selector) if start <= pos < end]
    values = [v for v in declared if v is not None]
    return values[-1] if values else fallback


def _centres() -> list[tuple[float, float, float]]:
    rail = _blocks(RAIL)[0][1]
    rail_centre = _px(rail, "left") + _px(rail, "width") / 2

    base_step, base_end = _blocks(STEP_DOT)[0][1], _blocks(END_DOT)[0][1]
    step_width, end_width = _px(base_step, "width"), _px(base_end, "width")

    rows = []
    for padding, start, end in _breakpoints():
        step_left = _in_force(STEP_DOT, "left", start, end, _px(base_step, "left"))
        end_left = _in_force(END_DOT, "left", start, end, _px(base_end, "left"))
        rows.append(
            (
                rail_centre,
                padding + step_left + step_width / 2,
                padding + end_left + end_width / 2,
            )
        )
    return rows


def test_step_dots_are_centred_on_the_rail_at_every_breakpoint():
    for index, (rail, step, _) in enumerate(_centres()):
        assert step == rail, (
            f"step dot at breakpoint {index} sits {step - rail}px off the rail"
        )


def test_terminating_dot_is_centred_on_the_rail_at_every_breakpoint():
    for index, (rail, _, end) in enumerate(_centres()):
        assert end == rail, (
            f"terminating dot at breakpoint {index} sits {end - rail}px "
            "off the rail"
        )
